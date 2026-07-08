#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>

#include "sl_lidar.h"
#include "sl_lidar_driver.h"

#ifdef _WIN32
#include <Windows.h>
#define delay(x) ::Sleep(x)
#else
#include <unistd.h>
static inline void delay(sl_word_size_t ms) {
    while (ms >= 1000) {
        usleep(1000 * 1000);
        ms -= 1000;
    }
    if (ms != 0) {
        usleep(ms * 1000);
    }
}
#endif

using namespace sl;

static bool ctrl_c_pressed = false;
static void ctrlc(int) {
    ctrl_c_pressed = true;
}

static void print_usage(const char* program) {
    printf("Usage:\n");
    printf("  %s --channel --serial <port> [baudrate] [--hold]\n", program);
    printf("Example:\n");
    printf("  %s --channel --serial /dev/rplidar 115200\n", program);
    printf("  %s --channel --serial /dev/rplidar 115200 --hold\n", program);
}

int main(int argc, const char* argv[]) {
    const char* port = "/dev/rplidar";
    sl_u32 baudrate = 115200;
    bool hold = false;

    if (argc >= 4) {
        if (strcmp(argv[1], "--channel") != 0 ||
            (strcmp(argv[2], "--serial") != 0 && strcmp(argv[2], "-s") != 0)) {
            print_usage(argv[0]);
            return 1;
        }
        port = argv[3];
        if (argc >= 5 && strcmp(argv[4], "--hold") != 0) {
            baudrate = strtoul(argv[4], NULL, 10);
        }
        for (int i = 4; i < argc; ++i) {
            if (strcmp(argv[i], "--hold") == 0) {
                hold = true;
            }
        }
    } else if (argc != 1) {
        print_usage(argv[0]);
        return 1;
    }

    printf("RPLIDAR stop motor tool. port=%s baudrate=%u hold=%s\n", port, baudrate, hold ? "true" : "false");

    ILidarDriver* drv = *createLidarDriver();
    if (!drv) {
        fprintf(stderr, "Error, insufficient memory.\n");
        return 2;
    }

    IChannel* channel = *createSerialPortChannel(port, baudrate);
    if (!channel) {
        fprintf(stderr, "Error, cannot create serial channel.\n");
        delete drv;
        return 3;
    }

    sl_result op_result = drv->connect(channel);
    if (SL_IS_FAIL(op_result)) {
        fprintf(stderr, "Error, cannot connect to %s: %x\n", port, op_result);
        delete drv;
        return 4;
    }

    // Do not require getHealth/getDeviceInfo here. If the lidar is already in a
    // weird state, we still want to send stop/motor-off commands.
    op_result = drv->stop();
    if (SL_IS_FAIL(op_result)) {
        fprintf(stderr, "Warning, stop scan failed: %x\n", op_result);
    } else {
        printf("Scan stop command sent.\n");
    }

    delay(300);

    op_result = drv->setMotorSpeed(0);
    if (SL_IS_FAIL(op_result)) {
        fprintf(stderr, "Warning, setMotorSpeed(0) failed: %x\n", op_result);
    } else {
        printf("Motor speed set to 0.\n");
    }

    delay(500);

    if (hold) {
        signal(SIGINT, ctrlc);
        signal(SIGTERM, ctrlc);
        printf("Holding serial connection and keeping motor stopped. Stop with: pkill -f stop_motor\n");
        while (!ctrl_c_pressed) {
            drv->setMotorSpeed(0);
            delay(1000);
        }
        printf("Exiting hold mode.\n");
    }

    delete drv;
    printf("Done. If the motor restarts after exit, keep this tool running with --hold or control motor power in hardware.\n");
    return 0;
}
