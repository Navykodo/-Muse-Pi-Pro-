#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <termios.h>
#include <signal.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <errno.h>

#define DEFAULT_PORT "/dev/rfcomm0"
#define TCP_PORT 2579
#define MAX_CLIENTS 10

static const char *serial_port = DEFAULT_PORT;
int serial_fd;
int server_fd;
int client_fd = -1;
struct termios orig_termios;
int running = 1;

void cleanup(int sig) {
    running = 0;
    printf("\n退出\n");
    if (serial_fd >= 0) close(serial_fd);
    if (client_fd >= 0) close(client_fd);
    if (server_fd >= 0) close(server_fd);
    tcsetattr(0, TCSAFLUSH, &orig_termios);
    exit(0);
}

// 初始化串口
int init_serial(const char *port) {
    int fd = open(port, O_RDWR);
    if (fd < 0) {
        perror("串口打开失败");
        return -1;
    }
    
    struct termios tty;
    tcgetattr(fd, &tty);
    cfsetispeed(&tty, B9600);
    cfsetospeed(&tty, B9600);
    tty.c_cflag &= ~(PARENB | CSTOPB | CSIZE | CRTSCTS);
    tty.c_cflag |= CS8 | CREAD | CLOCAL;
    tty.c_iflag &= ~(IXON | IXOFF | ICRNL);
    tty.c_lflag &= ~(ICANON | ECHO | ECHOE | ISIG);
    tty.c_cc[VMIN] = 1;
    tty.c_cc[VTIME] = 0;
    tcsetattr(fd, TCSANOW, &tty);
    
    return fd;
}

// 发送字符串到串口
void send_to_serial(const char *data, int len) {
    if (serial_fd >= 0) {
        write(serial_fd, data, len);
        tcdrain(serial_fd);
    }
}

void send_char_to_serial(char c) {
    if (serial_fd >= 0) {
        write(serial_fd, &c, 1);
        tcdrain(serial_fd);
    }
}

void send_string_to_serial(const char *str) {
    if (serial_fd >= 0 && str != NULL) {
        write(serial_fd, str, strlen(str));
        tcdrain(serial_fd);
    }
}

// 初始化 TCP 服务器
int init_tcp_server(int port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        perror("socket 创建失败");
        return -1;
    }
    
    int opt = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
    
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port = htons(port);
    
    if (bind(fd, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
        perror("bind 失败");
        close(fd);
        return -1;
    }
    
    if (listen(fd, MAX_CLIENTS) < 0) {
        perror("listen 失败");
        close(fd);
        return -1;
    }
    
    printf("[TCP] 服务器启动，监听端口 %d\n", port);
    return fd;
}

void print_help() {
    printf("\n");
    printf("╔════════════════════════════════════════════╗\n");
    printf("║     蓝牙串口 TCP 代理服务器 (纯转发)       ║\n");
    printf("╠════════════════════════════════════════════╣\n");
    printf("║  按键映射:                                ║\n");
    printf("║    w → A (前进)    s → E (后退)          ║\n");
    printf("║    a → G (左转)    d → C (右转)          ║\n");
    printf("║    其他 → 原样透传                        ║\n");
    printf("║    q → 退出                              ║\n");
    printf("╠════════════════════════════════════════════╣\n");
    printf("║  TCP 端口: %d                            ║\n", TCP_PORT);
    printf("║  无超时，持续转发                         ║\n");
    printf("╚════════════════════════════════════════════╝\n");
    printf("\n");
}

int main(int argc, char **argv) {
    const char *env_port = getenv("CAR_CONTROL_DEVICE");

    if (argc > 1 && argv[1][0] != '\0') {
        serial_port = argv[1];
    } else if (env_port != NULL && env_port[0] != '\0') {
        serial_port = env_port;
    }

    signal(SIGINT, cleanup);
    signal(SIGTERM, cleanup);
    
    // -------- 初始化串口 --------
    serial_fd = init_serial(serial_port);
    if (serial_fd < 0) return 1;
    printf("[串口] %s 已打开 (9600 8N1)\n", serial_port);
    
    // -------- 发送 ZK 切换控制模式 --------
    printf("[串口] 发送 ZK 切换控制模式...\n");
    send_string_to_serial("ZK");
    usleep(100000);
    
    // -------- 初始化 TCP 服务器 --------
    server_fd = init_tcp_server(TCP_PORT);
    if (server_fd < 0) {
        close(serial_fd);
        return 1;
    }
    
    print_help();
    
    // 设置终端原始模式（本地调试用）
    tcgetattr(0, &orig_termios);
    struct termios raw = orig_termios;
    raw.c_lflag &= ~(ECHO | ICANON | ISIG);
    raw.c_iflag &= ~(IXON | ICRNL);
    raw.c_cc[VMIN] = 0;
    raw.c_cc[VTIME] = 0;
    tcsetattr(0, TCSANOW, &raw);
    
    // -------- 主循环 --------
    fd_set readfds;
    int max_fd;
    char buffer[1024];
    
    printf("[系统] 等待客户端连接...\n\n");
    
    while (running) {
        FD_ZERO(&readfds);
        FD_SET(server_fd, &readfds);
        FD_SET(0, &readfds);
        max_fd = server_fd;
        
        if (client_fd >= 0) {
            FD_SET(client_fd, &readfds);
            if (client_fd > max_fd) max_fd = client_fd;
        }
        
        struct timeval tv;
        tv.tv_sec = 0;
        tv.tv_usec = 50000;
        
        int activity = select(max_fd + 1, &readfds, NULL, NULL, &tv);
        
        if (activity < 0) {
            if (errno == EINTR) continue;
            perror("select 错误");
            break;
        }
        
        // -------- 处理新连接 --------
        if (FD_ISSET(server_fd, &readfds)) {
            struct sockaddr_in client_addr;
            socklen_t addr_len = sizeof(client_addr);
            int new_client = accept(server_fd, (struct sockaddr*)&client_addr, &addr_len);
            if (new_client >= 0) {
                if (client_fd >= 0) {
                    printf("[TCP] 关闭旧客户端连接\n");
                    close(client_fd);
                }
                client_fd = new_client;
                printf("[TCP] 客户端连接: %s:%d\n", 
                       inet_ntoa(client_addr.sin_addr), 
                       ntohs(client_addr.sin_port));
            }
        }
        
        // -------- 处理客户端数据 --------
        if (client_fd >= 0 && FD_ISSET(client_fd, &readfds)) {
            int n = read(client_fd, buffer, sizeof(buffer) - 1);
            if (n <= 0) {
                printf("[TCP] 客户端断开连接\n");
                close(client_fd);
                client_fd = -1;
            } else {
                for (int i = 0; i < n; i++) {
                    char key = buffer[i];
                    
                    // 退出
                    if (key == 'q' || key == 'Q') {
                        printf("[TCP] 收到退出命令\n");
                        cleanup(0);
                    }
                    
                    // 映射并发送
                    char send_char;
                    switch (key) {
                        case 'w': case 'W': send_char = 'A'; printf("[映射] w → A (前进)\n"); break;
                        case 's': case 'S': send_char = 'E'; printf("[映射] s → E (后退)\n"); break;
                        case 'a': case 'A': send_char = 'G'; printf("[映射] a → G (左转)\n"); break;
                        case 'd': case 'D': send_char = 'C'; printf("[映射] d → C (右转)\n"); break;
                        default:            send_char = key; 
                                            if (key >= 0x20 && key <= 0x7E) 
                                                printf("[透传] 发送 %c\n", key);
                                            else if (key == '\r') 
                                                printf("[透传] 发送回车\n");
                                            else if (key == '\n') 
                                                printf("[透传] 发送换行\n");
                                            else 
                                                printf("[透传] 发送 0x%02X\n", (unsigned char)key);
                                            break;
                    }
                    send_char_to_serial(send_char);
                }
            }
        }
        
        // -------- 处理本地键盘输入 --------
        if (FD_ISSET(0, &readfds)) {
            char local_key;
            if (read(0, &local_key, 1) == 1) {
                if (local_key == 'q' || local_key == 'Q' || local_key == 0x03) {
                    cleanup(0);
                }
                char send_char;
                switch (local_key) {
                    case 'w': case 'W': send_char = 'A'; printf("[映射] w → A (前进)\n"); break;
                    case 's': case 'S': send_char = 'E'; printf("[映射] s → E (后退)\n"); break;
                    case 'a': case 'A': send_char = 'G'; printf("[映射] a → G (左转)\n"); break;
                    case 'd': case 'D': send_char = 'C'; printf("[映射] d → C (右转)\n"); break;
                    default:            send_char = local_key; 
                                        if (local_key >= 0x20 && local_key <= 0x7E) 
                                            printf("[透传] 发送 %c\n", local_key);
                                        else if (local_key == '\r') 
                                            printf("[透传] 发送回车\n");
                                        else if (local_key == '\n') 
                                            printf("[透传] 发送换行\n");
                                        else 
                                            printf("[透传] 发送 0x%02X\n", (unsigned char)local_key);
                                        break;
                }
                send_char_to_serial(send_char);
            }
        }
    }
    
    cleanup(0);
    return 0;
}
