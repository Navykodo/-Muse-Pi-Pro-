/**
 * 串口通信测试程序 - 小车速度控制
 * 功能：通过串口向小车发送速度控制指令，支持交互式命令行操作
 */

#include <stdio.h>      // 标准输入输出
#include <stdlib.h>     // 标准库函数
#include <string.h>     // 字符串处理
#include <unistd.h>     // UNIX系统调用
#include <fcntl.h>      // 文件控制选项
#include <errno.h>      // 错误码定义
#include <termios.h>    // 串口终端控制
#include <signal.h>     // 信号处理
#include <stdint.h>     // 标准整数类型
#include <sys/ioctl.h>  // I/O控制命令

#define Z_AXIS_TEST_SECONDS 3
#define Z_AXIS_TURN_SPEED 550
#define Z_AXIS_CALIBRATED_DEGREES 90
#define Z_AXIS_MIN_TURN_SECONDS 3
#define USEC_PER_SECOND 1000000LL

int fd;             // 串口文件描述符
int keep_running = 1; // 程序运行标志，用于信号处理退出循环

/**
 * 信号处理函数 - 捕获Ctrl+C(SIGINT)信号
 * @param sig: 信号编号
 */
void sigint_handler(int sig) {
    (void)sig;
    keep_running = 0;  // 设置退出标志，使主循环正常结束
}

/**
 * 配置串口参数
 * @param fd: 串口文件描述符
 * @return: 0表示成功
 * 
 * 配置说明:
 * - 波特率: 115200
 * - 数据位: 8位 (CS8)
 * - 校验: 无 (IGNPAR忽略奇偶校验错误)
 * - 停止位: 1位 (默认)
 * - 流控: 无 (CLOCAL本地模式，CREAD启用接收)
 * - 原始模式: 无输入输出处理
 */
int set_opt(int fd) {
    struct termios newtio;
    
    tcgetattr(fd, &newtio);  // 获取当前串口配置
    cfmakeraw(&newtio);      // 设置为原始模式(无缓冲、无处理)
    
    // c_cflag: 控制标志 - 设置波特率、数据位、本地模式、接收使能
    newtio.c_cflag = B115200 | CS8 | CLOCAL | CREAD;
    // c_iflag: 输入标志 - 忽略奇偶校验错误
    newtio.c_iflag = IGNPAR;
    // c_oflag: 输出标志 - 无特殊处理
    newtio.c_oflag = 0;
    // c_lflag: 本地标志 - 无回显、无规范模式
    newtio.c_lflag = 0;
    // c_cc: 控制字符 - 非阻塞读取(VMIN=0立即返回，VTIME=0无限等待)
    newtio.c_cc[VMIN] = 0;   // 最小读取字符数：0
    newtio.c_cc[VTIME] = 0;  // 读取超时时间：0

    tcflush(fd, TCIOFLUSH);           // 清空输入输出缓冲区
    tcsetattr(fd, TCSANOW, &newtio);  // 立即应用新配置(TCSANOW)
    return 0;
}

/**
 * 计算校验和 - 对帧头到速度字段进行XOR异或校验
 * @param frame: 数据帧指针(至少11字节)
 * @return: XOR校验结果
 */
uint8_t calc_xor(const uint8_t *frame) {
    uint8_t result = 0;
    for (int i = 0; i < 9; i++) result ^= frame[i];  // 异或第0-8字节
    return result;
}

/**
 * 发送速度控制帧
 * @param fd: 串口文件描述符
 * @param vx: X方向速度 (-32768 ~ 32767)
 * @param vy: Y方向速度 (-32768 ~ 32767)
 * @param vz: Z方向速度/旋转 (-32768 ~ 32767)
 * 
 * 帧格式(11字节):
 * [0]  0x7B     - 帧头
 * [1]  0x00     - 保留
 * [2]  0x00     - 保留
 * [3-4] vx     - X速度高字节、低字节(int16_t)
 * [5-6] vy     - Y速度高字节、低字节
 * [7-8] vz     - Z速度高字节、低字节
 * [9]  XOR     - 第0-8字节异或校验和
 * [10] 0x7D    - 帧尾
 */
void send_frame(int fd, int16_t vx, int16_t vy, int16_t vz) {
    uint8_t frame[11];
    
    frame[0] = 0x7B;                    // 帧头
    frame[1] = 0x00;                    // 保留字节1
    frame[2] = 0x00;                    // 保留字节2
    frame[3] = (vx >> 8) & 0xFF;        // X速度高字节
    frame[4] = vx & 0xFF;               // X速度低字节
    frame[5] = (vy >> 8) & 0xFF;        // Y速度高字节
    frame[6] = vy & 0xFF;               // Y速度低字节
    frame[7] = (vz >> 8) & 0xFF;        // Z速度高字节
    frame[8] = vz & 0xFF;               // Z速度低字节
    frame[9] = calc_xor(frame);         // 计算校验和
    frame[10] = 0x7D;                   // 帧尾
    
    // 发送前检查输出缓冲区状态(TIOCOUTQ获取待发字节数)
    int waiting;
    ioctl(fd, TIOCOUTQ, &waiting);
    if (waiting > 0) {
        printf("  输出缓冲有%d字节待发送，等待清空...\n", waiting);
        tcdrain(fd);  // 等待缓冲区数据发送完毕
    }
    
    int written = write(fd, frame, 11);  // 写入11字节数据
    tcdrain(fd);  // 确保数据完全发送(阻塞直到传输完成)
    
    printf("[Tx] ");
    for (int i = 0; i < 11; i++) printf("%02X ", frame[i]);
    printf("| V=(%d,%d,%d) | 已发送%d字节\n", vx, vy, vz, written);
}

void sleep_us_interruptible(long long total_us) {
    while (total_us > 0 && keep_running) {
        useconds_t chunk = total_us > 100000 ? 100000 : (useconds_t)total_us;
        usleep(chunk);
        total_us -= chunk;
    }
}

/**
 * 前进函数 - 小车向前移动指定距离
 * @param fd: 串口文件描述符
 * @param distance: 移动距离(cm)
 * @param speed: 移动速度(cm/s)
 * 
 * 逻辑：计算时间=distance/speed，发送速度(100,0,0)等待后停止
 */
void move_forward(int fd, int distance, int speed) {
    if (distance <= 0) distance = 50;  // 默认50cm
    if (speed <= 0) speed = 10;        // 默认10cm/s
    
    // 速度限制：不能超过距离的三分之一，否则误差太大
    int max_speed = distance / 3;
    if (max_speed < 1) max_speed = 1;
    if (speed > max_speed) {
        printf("速度%dcm/s过高，已自动调整为%dcm/s (距离/3)\n", speed, max_speed);
        speed = max_speed;
    }
    
    int seconds = distance / speed;
    if (seconds < 1) seconds = 1;
    
    // 速度值 = speed * 10 (因为参数/10=cm/s)
    int vx = speed * 10;
    printf("前进%dcm，速度%dcm/s，预计%d秒...\n", distance, speed, seconds);
    send_frame(fd, vx, 0, 0);
    sleep(seconds);
    send_frame(fd, 0, 0, 0);
    printf("前进完成\n");
}

/**
 * 后退函数 - 小车向后移动指定距离
 * @param fd: 串口文件描述符
 * @param distance: 移动距离(cm)
 * @param speed: 移动速度(cm/s)
 * 
 * 逻辑：计算时间=distance/speed，发送速度(-100,0,0)等待后停止
 */
void move_backward(int fd, int distance, int speed) {
    if (distance <= 0) distance = 50;  // 默认50cm
    if (speed <= 0) speed = 10;        // 默认10cm/s
    
    // 速度限制：不能超过距离的三分之一
    int max_speed = distance / 3;
    if (max_speed < 1) max_speed = 1;
    if (speed > max_speed) {
        printf("速度%dcm/s过高，已自动调整为%dcm/s (距离/3)\n", speed, max_speed);
        speed = max_speed;
    }
    
    int seconds = distance / speed;
    if (seconds < 1) seconds = 1;
    
    int vx = -speed * 10;
    printf("后退%dcm，速度%dcm/s，预计%d秒...\n", distance, speed, seconds);
    send_frame(fd, vx, 0, 0);
    sleep(seconds);
    send_frame(fd, 0, 0, 0);
    printf("后退完成\n");
}

/**
 * 左移函数 - 小车向左移动指定距离
 * @param fd: 串口文件描述符
 * @param distance: 移动距离(cm)
 * @param speed: 移动速度(cm/s)
 * 
 * 逻辑：计算时间=distance/speed，发送速度(0,100,0)等待后停止
 */
void move_left(int fd, int distance, int speed) {
    if (distance <= 0) distance = 20;  // 默认20cm
    if (speed <= 0) speed = 10;         // 默认10cm/s
    
    // 速度限制：不能超过距离的三分之一
    int max_speed = distance / 3;
    if (max_speed < 1) max_speed = 1;
    if (speed > max_speed) {
        printf("速度%dcm/s过高，已自动调整为%dcm/s (距离/3)\n", speed, max_speed);
        speed = max_speed;
    }
    
    int seconds = distance / speed;
    if (seconds < 1) seconds = 1;
    
    int vy = speed * 10;
    printf("左移%dcm，速度%dcm/s，预计%d秒...\n", distance, speed, seconds);
    send_frame(fd, 0, vy, 0);
    sleep(seconds);
    send_frame(fd, 0, 0, 0);
    printf("左移完成\n");
}

/**
 * 右移函数 - 小车向右移动指定距离
 * @param fd: 串口文件描述符
 * @param distance: 移动距离(cm)
 * @param speed: 移动速度(cm/s)
 * 
 * 逻辑：计算时间=distance/speed，发送速度(0,-100,0)等待后停止
 */
void move_right(int fd, int distance, int speed) {
    if (distance <= 0) distance = 20;  // 默认20cm
    if (speed <= 0) speed = 10;         // 默认10cm/s
    
    // 速度限制：不能超过距离的三分之一
    int max_speed = distance / 3;
    if (max_speed < 1) max_speed = 1;
    if (speed > max_speed) {
        printf("速度%dcm/s过高，已自动调整为%dcm/s (距离/3)\n", speed, max_speed);
        speed = max_speed;
    }
    
    int seconds = distance / speed;
    if (seconds < 1) seconds = 1;
    
    int vy = -speed * 10;
    printf("右移%dcm，速度%dcm/s，预计%d秒...\n", distance, speed, seconds);
    send_frame(fd, 0, vy, 0);
    sleep(seconds);
    send_frame(fd, 0, 0, 0);
    printf("右移完成\n");
}

/**
 * Z轴旋转测试接口 - 固定3秒发送指定Z轴速度，便于记录角度和速度关系
 * @param fd: 串口文件描述符
 * @param z_speed: Z轴速度/旋转原始值 (-32768 ~ 32767)
 */
void test_z_axis(int fd, int z_speed) {
    if (z_speed < -32768 || z_speed > 32767) {
        printf("Z轴速度范围: -32768 ~ 32767\n");
        return;
    }

    printf("Z轴测试: vz=%d，固定%d秒...\n", z_speed, Z_AXIS_TEST_SECONDS);
    printf("请记录实际旋转角度；实际角速度 = 角度 / %d 秒\n", Z_AXIS_TEST_SECONDS);

    tcflush(fd, TCIFLUSH);
    send_frame(fd, 0, 0, (int16_t)z_speed);
    sleep(Z_AXIS_TEST_SECONDS);
    send_frame(fd, 0, 0, 0);

    printf("Z轴测试完成，已停止。vz=%d，持续%d秒\n", z_speed, Z_AXIS_TEST_SECONDS);
}

/**
 * Z轴按角度旋转 - 正数逆时针，负数顺时针
 * 标定值：vz=550持续3秒约等于逆时针90度。
 * @param fd: 串口文件描述符
 * @param angle_degrees: 旋转角度，正数逆时针，负数顺时针
 */
void turn_z_angle(int fd, int angle_degrees) {
    long long angle = angle_degrees;
    long long abs_angle;
    long long duration_us;
    long long full_speed_duration_us;
    long long min_duration_us = Z_AXIS_MIN_TURN_SECONDS * USEC_PER_SECOND;
    long long speed_magnitude = Z_AXIS_TURN_SPEED;
    int16_t z_speed;

    if (angle_degrees == 0) {
        send_frame(fd, 0, 0, 0);
        printf("角度为0，已发送停止帧\n");
        return;
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

    printf("Z轴按角度旋转: %d度，方向=%s，vz=%d，预计%.3f秒",
           angle_degrees,
           angle > 0 ? "逆时针" : "顺时针",
           z_speed,
           duration_us / 1000000.0);
    if (full_speed_duration_us < min_duration_us) {
        printf("，小角度已降速");
    }
    printf("...\n");

    tcflush(fd, TCIFLUSH);
    send_frame(fd, 0, 0, z_speed);
    sleep_us_interruptible(duration_us);
    send_frame(fd, 0, 0, 0);

    printf("Z轴角度旋转完成，已停止。目标角度=%d度\n", angle_degrees);
}

/**
 * 非阻塞读取串口数据
 * @param fd: 串口文件描述符
 * 
 * 检查是否有设备返回的上行数据，以十六进制格式显示
 */
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

/**
 * 主函数
 * @param argc: 参数个数
 * @param argv: 参数数组，argv[1]为串口设备路径(如 /dev/ttyUSB0)
 * @return: 0正常退出，-1错误退出
 */
int main(int argc, char *argv[]) {
    int vx, vy, vz;        // 速度分量
    char input[256];       // 用户输入缓冲区
    int frame_count = 0;   // 发送帧计数器
    
    // 注册SIGINT信号处理函数(Ctrl+C)
    signal(SIGINT, sigint_handler);

    if (argc < 2) {
        printf("Usage: %s <serial_device>\n", argv[0]);
        return -1;
    }

    // 以读写模式打开串口设备
    // O_RDWR: 读写模式 | O_NOCTTY: 不作为控制终端 | O_NONBLOCK: 非阻塞
    fd = open(argv[1], O_RDWR | O_NOCTTY | O_NONBLOCK);
    if (fd < 0) {
        perror("open");  // 打印打开失败原因
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
    
    // 发送两帧零速度测试帧，确保设备进入可接收状态
    send_frame(fd, 0, 0, 0);
    usleep(100000);  // 延时100ms
    send_frame(fd, 0, 0, 0);
    usleep(100000);
    
    printf("\n2. 检查是否有上行数据...\n");
    check_rx(fd);
    usleep(50000);
    check_rx(fd);
    
    printf("\n提示:\n");
    printf("- 如果上面[Rx]收到数据，说明通信双向正常\n");
    printf("- 如果无回应但有数据灯闪烁，可能是半双工冲突\n");
    printf("- 输入 'forward <距离(cm)> <速度(cm/s)>' 前进\n");
    printf("  例: forward 100 100 -> 前进100cm，速度100cm/s\n");
    printf("  例: forward 50 10   -> 前进50cm，速度10cm/s\n");
    printf("- 输入 'backward <距离> <速度>' 后退\n");
    printf("- 输入 'left <距离> <速度>' 左移\n");
    printf("- 输入 'right <距离> <速度>' 右移\n");
    printf("- 输入 'z <z速度>' 或 'rotate <z速度>' Z轴旋转测试，固定3秒后停止\n");
    printf("  例: z 300     -> vz=300旋转3秒；z -300反向旋转3秒\n");
    printf("- 输入 'turn <角度>' 按角度旋转，正数逆时针，负数顺时针\n");
    printf("  例: turn 90   -> 逆时针约90度；turn -45 -> 顺时针约45度\n");
    printf("  小于90度会固定至少3秒，并按角度比例降低Z轴速度\n");
    printf("- 输入 'flush' 清空串口缓冲\n");
    printf("- 输入 'test' 连续发5帧测试\n");
    printf("- 输入 'quit' 退出\n\n");

    while (keep_running) {
        printf("> ");
        fflush(stdout);
        
        // 读取用户输入
        if (fgets(input, sizeof(input), stdin) == NULL) break;
        
        // 去除末尾换行符
        input[strcspn(input, "\n")] = '\0';
        if (strlen(input) == 0) continue;  // 空行跳过
        
        if (strcmp(input, "quit") == 0 || strcmp(input, "exit") == 0) {
            break;
        }
        else if (strcmp(input, "flush") == 0) {
            tcflush(fd, TCIOFLUSH);
            printf("缓冲区已清空\n");
            continue;
        }
        else if (strncmp(input, "forward", 7) == 0) {
            int distance = 0, speed = 0;
            sscanf(input, "forward %d %d", &distance, &speed);
            move_forward(fd, distance, speed);
            continue;
        }
        else if (strncmp(input, "backward", 8) == 0) {
            int distance = 0, speed = 0;
            sscanf(input, "backward %d %d", &distance, &speed);
            move_backward(fd, distance, speed);
            continue;
        }
        else if (strncmp(input, "left", 4) == 0) {
            int distance = 0, speed = 0;
            sscanf(input, "left %d %d", &distance, &speed);
            move_left(fd, distance, speed);
            continue;
        }
        else if (strncmp(input, "right", 5) == 0) {
            int distance = 0, speed = 0;
            sscanf(input, "right %d %d", &distance, &speed);
            move_right(fd, distance, speed);
            continue;
        }
        else if (strcmp(input, "z") == 0 || strncmp(input, "z ", 2) == 0 ||
                 strcmp(input, "rotate") == 0 || strncmp(input, "rotate ", 7) == 0) {
            int z_speed = 0;
            int parsed;

            if (input[0] == 'z') {
                parsed = sscanf(input, "z %d", &z_speed);
            } else {
                parsed = sscanf(input, "rotate %d", &z_speed);
            }

            if (parsed != 1) {
                printf("格式: z <z速度>，范围 -32768 ~ 32767，例: z 300\n");
                continue;
            }

            test_z_axis(fd, z_speed);
            continue;
        }
        else if (strcmp(input, "turn") == 0 || strncmp(input, "turn ", 5) == 0) {
            int angle = 0;
            int parsed = sscanf(input, "turn %d", &angle);

            if (parsed != 1) {
                printf("格式: turn <角度>，正数逆时针，负数顺时针，例: turn 90 或 turn -90\n");
                continue;
            }

            turn_z_angle(fd, angle);
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
        
        // 解析4个参数: vx vy vz distance，或3个参数: vx vy vz
        int distance = 0;
        int parsed = sscanf(input, "%d %d %d %d", &vx, &vy, &vz, &distance);
        
        if (parsed >= 3) {
            // 检查int16_t范围 (-32768 ~ 32767)
            if (vx < -32768 || vx > 32767 || vy < -32768 || vy > 32767 || vz < -32768 || vz > 32767) {
                printf("范围: -32768 ~ 32767\n");
                continue;
            }
            
            // 发送前清空输入缓冲区，防止设备上行数据干扰
            tcflush(fd, TCIFLUSH);
            
            frame_count++;
            printf("#%d: ", frame_count);
            send_frame(fd, (int16_t)vx, (int16_t)vy, (int16_t)vz);
            
            // 如果提供了距离参数，计算sleep时间并自动停止
            if (parsed == 4 && distance > 0) {
                // 速度 = |vx| / 10 (cm/s)，时间 = 距离 / 速度
                int speed = abs(vx) / 10;  // cm/s
                if (speed == 0) speed = abs(vy) / 10;  // 如果vx为0，用vy
                if (speed == 0) speed = abs(vz) / 10;  // 如果vy也为0，用vz
                
                if (speed > 0) {
                    int sleep_time = distance / speed;  // 秒
                    if (sleep_time < 1) sleep_time = 1;  // 至少1秒
                    printf("速度%dcm/s, 距离%dcm, sleep%d秒...\n", speed, distance, sleep_time);
                    sleep(sleep_time);
                    send_frame(fd, 0, 0, 0);
                    printf("移动完成，已停止\n");
                } else {
                    printf("速度为0，无法计算移动时间\n");
                }
            } else {
                // 无距离参数，等待50ms后检查回应
                usleep(50000);
                check_rx(fd);
            }
        }
        else {
            printf("格式: <vx> <vy> <vz> [距离(cm)]\n");
            printf("  例: 100 0 0 100   (10cm/s前进100cm)\n");
            printf("  例: 1000 0 0 100  (100cm/s前进100cm)\n");
        }
    }

    // 程序退出前发送停止帧，确保小车停止运动
    printf("\n发送停止帧...\n");
    tcflush(fd, TCIFLUSH);   // 清空输入缓冲区
    send_frame(fd, 0, 0, 0); // 发送零速停止帧
    tcdrain(fd);             // 等待发送完成
    close(fd);               // 关闭串口
    printf("已退出\n");
    return 0;
}

