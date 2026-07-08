#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <termios.h>
#include <signal.h>
#include <stdint.h>
#include <sys/ioctl.h>

int fd;
int keep_running = 1;

void sigint_handler(int sig) {
    keep_running = 0;
}

int set_opt(int fd) {
    struct termios newtio;
    
    tcgetattr(fd, &newtio);
    cfmakeraw(&newtio);
    
    newtio.c_cflag = B115200 | CS8 | CLOCAL | CREAD;
    newtio.c_iflag = IGNPAR;
    newtio.c_oflag = 0;
    newtio.c_lflag = 0;
    newtio.c_cc[VMIN] = 0;
    newtio.c_cc[VTIME] = 0;

    tcflush(fd, TCIOFLUSH);
    tcsetattr(fd, TCSANOW, &newtio);
    return 0;
}

uint8_t calc_xor(uint8_t *frame) {
    uint8_t result = 0;
    for (int i = 0; i < 10; i++) result ^= frame[i];
    return result;
}

void send_frame(int fd, int16_t vx, int16_t vy, int16_t vz) {
    uint8_t frame[11];
    
    frame[0] = 0x7B;
    frame[1] = 0x00;
    frame[2] = 0x00;
    frame[3] = (vx >> 8) & 0xFF;
    frame[4] = vx & 0xFF;
    frame[5] = (vy >> 8) & 0xFF;
    frame[6] = vy & 0xFF;
    frame[7] = (vz >> 8) & 0xFF;
    frame[8] = vz & 0xFF;
    frame[9] = calc_xor(frame);
    frame[10] = 0x7D;
    
    // 发送前检查输出缓冲区
    int waiting;
    ioctl(fd, TIOCOUTQ, &waiting);
    if (waiting > 0) {
        printf("  输出缓冲有%d字节待发送，等待清空...\n", waiting);
        tcdrain(fd);
    }
    
    int written = write(fd, frame, 11);
    tcdrain(fd);  // 等待发送完成
    
    printf("[Tx] ");
    for (int i = 0; i < 11; i++) printf("%02X ", frame[i]);
    printf("| V=(%d,%d,%d) | 已发送%d字节\n", vx, vy, vz, written);
}

// 非阻塞读取，看有没有数据回来
void check_rx(int fd) {
    uint8_t buf[256];
    int n = read(fd, buf, sizeof(buf));
    if (n > 0) {
        printf("[Rx] 收到%d字节: ", n);
        if (n > 24) n = 24;  // 只显示前面
        for (int i = 0; i < n; i++) printf("%02X ", buf[i]);
        printf("\n");
    } else if (n == 0) {
        printf("[Rx] 无回应\n");
    } else {
        printf("[Rx] 读取错误: %s\n", strerror(errno));
    }
}

int main(int argc, char *argv[]) {
    int vx, vy, vz;
    char input[256];
    int frame_count = 0;
    
    signal(SIGINT, sigint_handler);

    if (argc < 2) {
        printf("Usage: %s <serial_device>\n", argv[0]);
        return -1;
    }

    fd = open(argv[1], O_RDWR | O_NOCTTY | O_NONBLOCK);  // 非阻塞打开
    if (fd < 0) {
        perror("open");
        return -1;
    }

    set_opt(fd);
    
    // 清空所有缓冲区
    tcflush(fd, TCIOFLUSH);
    
    // 等待设备稳定
    printf("等待设备初始化...\n");
    usleep(500000);
    
    // 清空可能积累的输入数据
    tcflush(fd, TCIFLUSH);
    
    printf("\n══════════════════════════════\n");
    printf("  小车速度控制 v3.0 (诊断版)\n");
    printf("  %s @ 115200\n", argv[1]);
    printf("══════════════════════════════\n\n");
    
    printf("诊断步骤:\n");
    printf("1. 发送零速帧测试通信\n");
    
    // 发两帧零速，确保设备收到
    send_frame(fd, 0, 0, 0);
    usleep(100000);
    send_frame(fd, 0, 0, 0);
    usleep(100000);
    
    printf("\n2. 检查是否有上行数据...\n");
    check_rx(fd);
    usleep(50000);
    check_rx(fd);
    
    printf("\n提示:\n");
    printf("- 如果上面[Rx]收到数据，说明通信双向正常\n");
    printf("- 如果无回应但有数据灯闪烁，可能是半双工冲突\n");
    printf("- 输入 'flush' 清空串口缓冲\n");
    printf("- 输入 'test' 连续发5帧测试\n");
    printf("- 输入 X Y Z 发送速度指令\n");
    printf("- 输入 'quit' 退出\n\n");

    while (keep_running) {
        printf("> ");
        fflush(stdout);
        
        if (fgets(input, sizeof(input), stdin) == NULL) break;
        
        input[strcspn(input, "\n")] = '\0';
        if (strlen(input) == 0) continue;
        
        if (strcmp(input, "quit") == 0 || strcmp(input, "exit") == 0) {
            break;
        }
        else if (strcmp(input, "flush") == 0) {
            tcflush(fd, TCIOFLUSH);
            printf("缓冲区已清空\n");
            continue;
        }
        else if (strcmp(input, "test") == 0) {
            printf("连续发送测试:\n");
            for (int i = 0; i < 5; i++) {
                frame_count++;
                printf("#%d: ", frame_count);
                send_frame(fd, 0, 0, 0);
                usleep(50000);
            }
            printf("测试完成\n");
            continue;
        }
        
        if (sscanf(input, "%d %d %d", &vx, &vy, &vz) == 3) {
            if (vx < -32768 || vx > 32767 || vy < -32768 || vy > 32767 || vz < -32768 || vz > 32767) {
                printf("范围: -32768 ~ 32767\n");
                continue;
            }
            
            // 发送前清输入缓冲（设备上行数据可能干扰）
            tcflush(fd, TCIFLUSH);
            
            frame_count++;
            printf("#%d: ", frame_count);
            send_frame(fd, (int16_t)vx, (int16_t)vy, (int16_t)vz);
            
            // 等待一小段时间看回应
            usleep(50000);
            check_rx(fd);
        }
        else {
            printf("格式: X Y Z (如: 100 -50 0)\n");
        }
    }

    printf("\n发送停止帧...\n");
    tcflush(fd, TCIFLUSH);
    send_frame(fd, 0, 0, 0);
    tcdrain(fd);
    close(fd);
    printf("已退出\n");
    return 0;
}

