/**
 * 小车运动控制后台服务
 *
 * 用法:
 *   sudo ./car_move
 *   sudo ./car_move_with_turn /dev/ttyUSB1
 *
 * 程序会在后台静默运行，固定监听 127.0.0.1:5555。
 * 后续程序可以向端口发送一行文本命令:
 *   forward 100 20
 *   backward 50 10
 *   left 20 10
 *   right 20 10
 *   turn 90
 *   turn -45
 *   stop
 */

#include <arpa/inet.h>
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <termios.h>
#include <unistd.h>

#define DEFAULT_SERIAL_DEVICE "/dev/ttyUSB1"
#define LISTEN_HOST "127.0.0.1"
#define LISTEN_PORT 5555
#define COMMAND_SIZE 256
#define RESPONSE_SIZE 256
#define Z_AXIS_TEST_SECONDS 3
#define Z_AXIS_TURN_SPEED 550
#define Z_AXIS_CALIBRATED_DEGREES 90
#define Z_AXIS_MIN_TURN_SECONDS 3
#define USEC_PER_SECOND 1000000LL

static const char *serial_device = DEFAULT_SERIAL_DEVICE;
static int fd = -1;
static int listen_fd = -1;
static volatile sig_atomic_t keep_running = 1;
static pthread_mutex_t serial_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t motion_mutex = PTHREAD_MUTEX_INITIALIZER;
static unsigned int stop_generation = 0;
static int motion_active = 0;

static void handle_signal(int sig) {
    int old_listen_fd = listen_fd;

    (void)sig;
    keep_running = 0;
    listen_fd = -1;
    if (old_listen_fd >= 0) close(old_listen_fd);
}

static int setup_serial(void) {
    struct termios newtio;

    fd = open(serial_device, O_RDWR | O_NOCTTY);
    if (fd < 0) {
        perror("open serial");
        return -1;
    }

    if (tcgetattr(fd, &newtio) < 0) {
        perror("tcgetattr");
        close(fd);
        fd = -1;
        return -1;
    }

    cfmakeraw(&newtio);
    newtio.c_cflag = B115200 | CS8 | CLOCAL | CREAD;
    newtio.c_iflag = IGNPAR;
    newtio.c_oflag = 0;
    newtio.c_lflag = 0;
    newtio.c_cc[VMIN] = 0;
    newtio.c_cc[VTIME] = 0;

    tcflush(fd, TCIOFLUSH);
    if (tcsetattr(fd, TCSANOW, &newtio) < 0) {
        perror("tcsetattr");
        close(fd);
        fd = -1;
        return -1;
    }

    usleep(200000);
    tcflush(fd, TCIFLUSH);
    return 0;
}

static int send_frame(int16_t vx, int16_t vy, int16_t vz) {
    uint8_t frame[11];
    size_t sent = 0;

    frame[0] = 0x7B;
    frame[1] = 0x00;
    frame[2] = 0x00;
    frame[3] = (vx >> 8) & 0xFF;
    frame[4] = vx & 0xFF;
    frame[5] = (vy >> 8) & 0xFF;
    frame[6] = vy & 0xFF;
    frame[7] = (vz >> 8) & 0xFF;
    frame[8] = vz & 0xFF;
    frame[9] = 0;
    for (int i = 0; i < 9; i++) frame[9] ^= frame[i];
    frame[10] = 0x7D;

    while (sent < sizeof(frame)) {
        ssize_t n = write(fd, frame + sent, sizeof(frame) - sent);
        if (n < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        if (n == 0) return -1;
        sent += (size_t)n;
    }

    return tcdrain(fd);
}

static int send_frame_locked(int16_t vx, int16_t vy, int16_t vz) {
    int result;

    pthread_mutex_lock(&serial_mutex);
    result = send_frame(vx, vy, vz);
    pthread_mutex_unlock(&serial_mutex);
    return result;
}

static unsigned int current_stop_generation(void) {
    unsigned int generation;

    pthread_mutex_lock(&motion_mutex);
    generation = stop_generation;
    pthread_mutex_unlock(&motion_mutex);
    return generation;
}

static int wait_for_motion_or_stop(int seconds, unsigned int start_generation) {
    int ticks = seconds * 10;

    while (ticks-- > 0 && keep_running) {
        usleep(100000);
        if (current_stop_generation() != start_generation) return -1;
    }

    return keep_running ? 0 : -1;
}

static int wait_for_motion_us_or_stop(long long total_us, unsigned int start_generation) {
    while (total_us > 0 && keep_running) {
        useconds_t chunk = total_us > 100000 ? 100000 : (useconds_t)total_us;

        usleep(chunk);
        total_us -= chunk;
        if (current_stop_generation() != start_generation) return -1;
    }

    return keep_running ? 0 : -1;
}

static void finish_motion(void) {
    pthread_mutex_lock(&motion_mutex);
    motion_active = 0;
    pthread_mutex_unlock(&motion_mutex);
}

static int request_stop(void) {
    pthread_mutex_lock(&motion_mutex);
    stop_generation++;
    motion_active = 0;
    pthread_mutex_unlock(&motion_mutex);

    return send_frame_locked(0, 0, 0);
}

static int is_direction(const char *direction) {
    return strcmp(direction, "forward") == 0 ||
           strcmp(direction, "backward") == 0 ||
           strcmp(direction, "left") == 0 ||
           strcmp(direction, "right") == 0;
}

static int execute_move(const char *direction, int distance, int speed,
                        char *response, size_t response_size) {
    int16_t vx = 0;
    int16_t vy = 0;
    int max_speed;
    int seconds;
    unsigned int start_generation;

    if (!is_direction(direction)) {
        snprintf(response, response_size, "ERR unknown direction\n");
        return -1;
    }

    if (distance <= 0 || speed <= 0) {
        snprintf(response, response_size, "ERR distance and speed must be positive\n");
        return -1;
    }

    max_speed = distance / 3;
    if (max_speed < 1) max_speed = 1;
    if (speed > max_speed) speed = max_speed;

    seconds = distance / speed;
    if (seconds < 1) seconds = 1;

    if (strcmp(direction, "forward") == 0) {
        vx = (int16_t)(speed * 10);
    } else if (strcmp(direction, "backward") == 0) {
        vx = (int16_t)(-speed * 10);
    } else if (strcmp(direction, "left") == 0) {
        vy = (int16_t)(speed * 10);
    } else {
        vy = (int16_t)(-speed * 10);
    }

    pthread_mutex_lock(&motion_mutex);
    if (motion_active) {
        pthread_mutex_unlock(&motion_mutex);
        snprintf(response, response_size, "ERR motion already active\n");
        return -1;
    }
    motion_active = 1;
    start_generation = stop_generation;
    pthread_mutex_unlock(&motion_mutex);

    if (send_frame_locked(vx, vy, 0) < 0) {
        finish_motion();
        snprintf(response, response_size, "ERR failed to send start frame\n");
        return -1;
    }

    if (wait_for_motion_or_stop(seconds, start_generation) < 0) {
        send_frame_locked(0, 0, 0);
        finish_motion();
        snprintf(response, response_size, "OK stopped\n");
        return -1;
    }

    if (send_frame_locked(0, 0, 0) < 0) {
        finish_motion();
        snprintf(response, response_size, "ERR failed to send stop frame\n");
        return -1;
    }

    finish_motion();
    snprintf(response, response_size, "OK %s distance=%d speed=%d seconds=%d\n",
             direction, distance, speed, seconds);
    return 0;
}

static int execute_turn(int angle_degrees, char *response, size_t response_size) {
    long long angle = angle_degrees;
    long long abs_angle;
    long long duration_us;
    long long full_speed_duration_us;
    long long min_duration_us = Z_AXIS_MIN_TURN_SECONDS * USEC_PER_SECOND;
    long long speed_magnitude = Z_AXIS_TURN_SPEED;
    int16_t z_speed;
    unsigned int start_generation;

    if (angle_degrees == 0) {
        if (request_stop() < 0) {
            snprintf(response, response_size, "ERR failed to send stop frame\n");
            return -1;
        }
        snprintf(response, response_size, "OK turn angle=0 speed=0 duration_ms=0\n");
        return 0;
    }

    abs_angle = angle < 0 ? -angle : angle;
    full_speed_duration_us = (abs_angle * Z_AXIS_TEST_SECONDS * USEC_PER_SECOND +
                              Z_AXIS_CALIBRATED_DEGREES / 2) / Z_AXIS_CALIBRATED_DEGREES;

    if (full_speed_duration_us < min_duration_us) {
        duration_us = min_duration_us;
        speed_magnitude = (abs_angle * Z_AXIS_TURN_SPEED +
                           Z_AXIS_CALIBRATED_DEGREES / 2) / Z_AXIS_CALIBRATED_DEGREES;
        if (speed_magnitude < 1) speed_magnitude = 1;
    } else {
        duration_us = full_speed_duration_us;
    }

    z_speed = angle > 0 ? (int16_t)speed_magnitude : (int16_t)-speed_magnitude;

    pthread_mutex_lock(&motion_mutex);
    if (motion_active) {
        pthread_mutex_unlock(&motion_mutex);
        snprintf(response, response_size, "ERR motion already active\n");
        return -1;
    }
    motion_active = 1;
    start_generation = stop_generation;
    pthread_mutex_unlock(&motion_mutex);

    if (send_frame_locked(0, 0, z_speed) < 0) {
        finish_motion();
        snprintf(response, response_size, "ERR failed to send start frame\n");
        return -1;
    }

    if (wait_for_motion_us_or_stop(duration_us, start_generation) < 0) {
        send_frame_locked(0, 0, 0);
        finish_motion();
        snprintf(response, response_size, "OK stopped\n");
        return -1;
    }

    if (send_frame_locked(0, 0, 0) < 0) {
        finish_motion();
        snprintf(response, response_size, "ERR failed to send stop frame\n");
        return -1;
    }

    finish_motion();
    snprintf(response, response_size, "OK turn angle=%d speed=%d duration_ms=%lld\n",
             angle_degrees, z_speed, duration_us / 1000);
    return 0;
}

static int has_extra_text(const char *text) {
    while (*text) {
        if (!isspace((unsigned char)*text)) return 1;
        text++;
    }
    return 0;
}

static void strip_line_end(char *line) {
    line[strcspn(line, "\r\n")] = '\0';
}

static ssize_t recv_line(int sock, char *buffer, size_t size) {
    size_t used = 0;

    while (used + 1 < size) {
        char ch;
        ssize_t n = recv(sock, &ch, 1, 0);

        if (n < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        if (n == 0) break;

        buffer[used++] = ch;
        if (ch == '\n') break;
    }

    buffer[used] = '\0';
    return (ssize_t)used;
}

static int send_all(int sock, const char *data) {
    size_t len = strlen(data);
    size_t sent = 0;

    while (sent < len) {
        ssize_t n = send(sock, data + sent, len - sent, MSG_NOSIGNAL);
        if (n < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        if (n == 0) return -1;
        sent += (size_t)n;
    }

    return 0;
}

static int handle_command(const char *command, char *response, size_t response_size) {
    char op[32];
    int distance;
    int speed;
    int angle;
    int consumed = 0;

    if (sscanf(command, " %31s %n", op, &consumed) != 1) {
        snprintf(response, response_size, "ERR empty command\n");
        return -1;
    }

    if (strcmp(op, "ping") == 0) {
        snprintf(response, response_size,
                 has_extra_text(command + consumed) ? "ERR ping takes no arguments\n" : "OK pong\n");
        return response[0] == 'O' ? 0 : -1;
    }

    if (strcmp(op, "stop") == 0) {
        if (has_extra_text(command + consumed)) {
            snprintf(response, response_size, "ERR stop takes no arguments\n");
            return -1;
        }
        if (request_stop() < 0) {
            snprintf(response, response_size, "ERR failed to send stop frame\n");
            return -1;
        }
        snprintf(response, response_size, "OK stopped\n");
        return 0;
    }

    if (is_direction(op)) {
        const char *args = command + consumed;
        int args_consumed = 0;

        if (sscanf(args, " %d %d %n", &distance, &speed, &args_consumed) != 2) {
            snprintf(response, response_size,
                     "ERR format: forward|backward|left|right <distance_cm> <speed_cm_s>\n");
            return -1;
        }
        if (has_extra_text(args + args_consumed)) {
            snprintf(response, response_size, "ERR too many arguments\n");
            return -1;
        }

        return execute_move(op, distance, speed, response, response_size);
    }

    if (strcmp(op, "turn") == 0) {
        const char *args = command + consumed;
        int args_consumed = 0;

        if (sscanf(args, " %d %n", &angle, &args_consumed) != 1) {
            snprintf(response, response_size, "ERR format: turn <angle_degrees>\n");
            return -1;
        }
        if (has_extra_text(args + args_consumed)) {
            snprintf(response, response_size, "ERR too many arguments\n");
            return -1;
        }

        return execute_turn(angle, response, response_size);
    }

    snprintf(response, response_size, "ERR unsupported command\n");
    return -1;
}

static void handle_client(int client_fd) {
    char command[COMMAND_SIZE];
    char response[RESPONSE_SIZE];
    ssize_t n = recv_line(client_fd, command, sizeof(command));

    if (n <= 0) return;

    strip_line_end(command);
    handle_command(command, response, sizeof(response));
    send_all(client_fd, response);
}

static void *client_thread(void *arg) {
    int client_fd = *(int *)arg;

    free(arg);
    handle_client(client_fd);
    close(client_fd);
    return NULL;
}

static int setup_listener(void) {
    struct sockaddr_in addr;
    int reuse = 1;

    listen_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (listen_fd < 0) {
        perror("socket");
        return -1;
    }

    setsockopt(listen_fd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons(LISTEN_PORT);
    if (inet_pton(AF_INET, LISTEN_HOST, &addr.sin_addr) != 1) {
        fprintf(stderr, "invalid listen address\n");
        close(listen_fd);
        listen_fd = -1;
        return -1;
    }

    if (bind(listen_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("bind");
        close(listen_fd);
        listen_fd = -1;
        return -1;
    }

    if (listen(listen_fd, 8) < 0) {
        perror("listen");
        close(listen_fd);
        listen_fd = -1;
        return -1;
    }

    return 0;
}

static int run_in_background(void) {
    pid_t pid = fork();

    if (pid < 0) {
        perror("fork");
        return -1;
    }
    if (pid > 0) {
        return 1;
    }

    if (setsid() < 0) _exit(1);
    signal(SIGHUP, SIG_IGN);

    pid = fork();
    if (pid < 0) _exit(1);
    if (pid > 0) _exit(0);

    umask(0);
    chdir("/");

    close(STDIN_FILENO);
    close(STDOUT_FILENO);
    close(STDERR_FILENO);
    open("/dev/null", O_RDONLY);
    open("/dev/null", O_WRONLY);
    open("/dev/null", O_WRONLY);

    return 0;
}

static void event_loop(void) {
    while (keep_running) {
        int client_fd = accept(listen_fd, NULL, NULL);
        if (client_fd < 0) {
            if (errno == EINTR || !keep_running) continue;
            continue;
        }

        int *thread_client_fd = malloc(sizeof(*thread_client_fd));
        pthread_t thread;

        if (thread_client_fd == NULL) {
            close(client_fd);
            continue;
        }

        *thread_client_fd = client_fd;
        if (pthread_create(&thread, NULL, client_thread, thread_client_fd) != 0) {
            free(thread_client_fd);
            close(client_fd);
            continue;
        }
        pthread_detach(thread);
    }
}

int main(int argc, char **argv) {
    const char *env_device = getenv("CAR_SERIAL_DEVICE");

    if (argc > 1 && argv[1][0] != '\0') {
        serial_device = argv[1];
    } else if (env_device != NULL && env_device[0] != '\0') {
        serial_device = env_device;
    }

    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);

    if (setup_serial() < 0) return 1;
    if (setup_listener() < 0) {
        close(fd);
        return 1;
    }

    if (run_in_background() != 0) return 0;

    event_loop();
    send_frame_locked(0, 0, 0);

    if (listen_fd >= 0) close(listen_fd);
    if (fd >= 0) close(fd);
    return 0;
}
