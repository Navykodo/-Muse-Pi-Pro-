#include <linux/module.h>
#include <linux/gpio.h>
#include <linux/delay.h>
#include <linux/kobject.h>
#include <linux/sysfs.h>
#include <linux/kthread.h>
#include <linux/timekeeping.h>
#include <linux/fs.h>
#include <linux/slab.h>
#include <linux/uaccess.h>

#define DHT11_GPIO 71  // DHT11 数据线 GPIO

static struct kobject *dht11_kobj;
static struct task_struct *sensor_thread;

// --- DHT11 基本操作 ---
static void dht11_start_signal(void)
{
    gpio_direction_output(DHT11_GPIO, 0);
    msleep(20);           // >=18ms
    gpio_direction_input(DHT11_GPIO);
    udelay(30);           // 20~40us
}

static int dht11_check_response(void)
{
    int timeout = 0;
    // 等待拉低 ~80us
    while(gpio_get_value(DHT11_GPIO)) {
        udelay(1);
        if(++timeout > 100) return -1;
    }
    timeout = 0;
    // 等待拉高 ~80us
    while(!gpio_get_value(DHT11_GPIO)) {
        udelay(1);
        if(++timeout > 100) return -1;
    }
    timeout = 0;
    // 等待拉低开始数据传输
    while(gpio_get_value(DHT11_GPIO)) {
        udelay(1);
        if(++timeout > 100) return -1;
    }
    return 0;
}

static int dht11_read_bit(void)
{
    int timeout = 0;
    while(!gpio_get_value(DHT11_GPIO)) {
        udelay(1);
        if(++timeout > 100) return -1;
    }
    timeout = 0;
    while(gpio_get_value(DHT11_GPIO)) {
        udelay(1);
        timeout++;
        if(timeout > 100) return -1;
    }
    return timeout > 40 ? 1 : 0;
}

static int dht11_read_byte(void)
{
    int i, byte = 0, bit;
    for(i = 0; i < 8; i++) {
        bit = dht11_read_bit();
        if(bit < 0) return -1;
        byte = (byte << 1) | bit;
    }
    return byte;
}

static int dht11_read_data(int *temperature, int *humidity)
{
    int rh_int, rh_dec, t_int, t_dec, checksum;

    dht11_start_signal();
    if(dht11_check_response() < 0)
        return -1;

    rh_int = dht11_read_byte();
    rh_dec = dht11_read_byte();
    t_int  = dht11_read_byte();
    t_dec  = dht11_read_byte();
    checksum = dht11_read_byte();

    if(((rh_int + rh_dec + t_int + t_dec) & 0xFF) != checksum)
        return -1;

    *humidity = rh_int;
    *temperature = t_int;
    return 0;
}

// --- sysfs 接口 ---
static ssize_t temp_show(struct kobject *kobj, struct kobj_attribute *attr, char *buf)
{
    int temp, hum;
    if(dht11_read_data(&temp, &hum) < 0)
        return sprintf(buf, "Sensor not detected\n");
    return sprintf(buf, "Temperature: %d C, Humidity: %d %%\n", temp, hum);
}

static struct kobj_attribute temp_attribute = __ATTR(temp, 0444, temp_show, NULL);

// --- 内核线程: 定时读取并写日志 ---
static int sensor_thread_fn(void *data)
{
    char filepath[128];
    char buf[128];
    struct file *filp;
    loff_t pos;
    time64_t t;
    struct tm tm;
    int temp, hum;

    while(!kthread_should_stop()) {
        if(dht11_read_data(&temp, &hum) < 0) {
            temp = -1;
            hum = -1;
        }

        t = ktime_get_real_seconds();
        time64_to_tm(t, 8*3600, &tm); // 东八区

        snprintf(filepath, sizeof(filepath),
                 "/log/sensor_%04ld-%02d-%02d_%02d.txt",
                 tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday, tm.tm_hour);

        if(temp < 0)
            snprintf(buf, sizeof(buf), "%lld sensor_error\n", (long long)t);
        else
            snprintf(buf, sizeof(buf), "%lld Temp:%dC Hum:%d%%\n",
                     (long long)t, temp, hum);

        filp = filp_open(filepath, O_WRONLY | O_CREAT | O_APPEND, 0644);
        if(!IS_ERR(filp)) {
            pos = filp->f_pos;
            kernel_write(filp, buf, strlen(buf), &pos);
            filp_close(filp, NULL);
        }

        ssleep(5);
    }
    return 0;
}

// --- 初始化 ---
static int __init dht11_init(void)
{
    int ret;

    pr_alert("DHT11 module loading...\n");

    ret = gpio_request(DHT11_GPIO, "dht11");
    if(ret) {
        pr_err("Failed to request GPIO %d\n", DHT11_GPIO);
        return ret;
    }

    dht11_kobj = kobject_create_and_add("dht11", kernel_kobj);
    if(!dht11_kobj) {
        gpio_free(DHT11_GPIO);
        return -ENOMEM;
    }

    ret = sysfs_create_file(dht11_kobj, &temp_attribute.attr);
    if(ret) {
        kobject_put(dht11_kobj);
        gpio_free(DHT11_GPIO);
        return ret;
    }

    sensor_thread = kthread_run(sensor_thread_fn, NULL, "sensor_logger");
    if(IS_ERR(sensor_thread)) {
        pr_err("Failed to create sensor logging thread\n");
        sysfs_remove_file(dht11_kobj, &temp_attribute.attr);
        kobject_put(dht11_kobj);
        gpio_free(DHT11_GPIO);
        return PTR_ERR(sensor_thread);
    }

    pr_alert("DHT11 module loaded, read via /sys/kernel/dht11/temp\n");
    return 0;
}

// --- 卸载 ---
static void __exit dht11_exit(void)
{
    if(sensor_thread)
        kthread_stop(sensor_thread);

    sysfs_remove_file(dht11_kobj, &temp_attribute.attr);
    kobject_put(dht11_kobj);
    gpio_free(DHT11_GPIO);
    pr_alert("DHT11 module unloaded\n");
}

module_init(dht11_init);
module_exit(dht11_exit);

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Your Name");
MODULE_DESCRIPTION("DHT11 temperature and humidity logging module (East-8 timezone)");

