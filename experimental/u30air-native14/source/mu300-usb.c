/* SPDX-License-Identifier: GPL-2.0-only */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <glob.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static const char *sysroot;

static int read_value(const char *path, char *buf, size_t len)
{
	FILE *file = fopen(path, "r");
	if (!file)
		return -1;
	if (!fgets(buf, len, file)) {
		fclose(file);
		return -1;
	}
	fclose(file);
	buf[strcspn(buf, "\r\n")] = 0;
	return 0;
}

static int native_path(char *out, size_t len)
{
	glob_t matches = {0};
	char pattern[PATH_MAX], link[PATH_MAX];
	int found = -1;

	snprintf(pattern, sizeof(pattern), "%s/sys/bus/i2c/devices/*/driver", sysroot);
	if (glob(pattern, 0, NULL, &matches))
		return -1;
	for (size_t i = 0; i < matches.gl_pathc; i++) {
		ssize_t size = readlink(matches.gl_pathv[i], link, sizeof(link) - 1);
		if (size < 0)
			continue;
		link[size] = 0;
		char *name = strrchr(link, '/');
		name = name ? name + 1 : link;
		if (strcmp(name, "sgm41511-native"))
			continue;
		if (snprintf(out, len, "%s", matches.gl_pathv[i]) >= (int)len)
			continue;
		char *last = strrchr(out, '/');
		if (last)
			*last = 0;
		found = 0;
		break;
	}
	globfree(&matches);
	return found;
}

static int role_path(char *out, size_t len)
{
	glob_t matches = {0};
	char pattern[PATH_MAX];
	snprintf(pattern, sizeof(pattern), "%s/sys/class/usb_role/*/role", sysroot);
	if (glob(pattern, 0, NULL, &matches))
		return -1;
	int ret = -1;
	if (matches.gl_pathc == 1) {
		snprintf(out, len, "%s", matches.gl_pathv[0]);
		ret = 0;
	}
	globfree(&matches);
	return ret;
}

static void devices(void)
{
	glob_t matches = {0};
	char pattern[PATH_MAX], path[PATH_MAX + 32], product[256], vendor[32], id[32];
	snprintf(pattern, sizeof(pattern), "%s/sys/bus/usb/devices/*/product", sysroot);
	if (glob(pattern, 0, NULL, &matches))
		return;
	for (size_t i = 0; i < matches.gl_pathc; i++) {
		if (read_value(matches.gl_pathv[i], product, sizeof(product)))
			continue;
		snprintf(path, sizeof(path), "%s", matches.gl_pathv[i]);
		char *last = strrchr(path, '/');
		if (!last)
			continue;
		strcpy(last + 1, "idVendor");
		if (read_value(path, vendor, sizeof(vendor)))
			continue;
		strcpy(last + 1, "idProduct");
		if (!read_value(path, id, sizeof(id)))
			printf("  device: %s (%s:%s)\n", product, vendor, id);
	}
	globfree(&matches);
}

int main(int argc, char **argv)
{
	char charger[PATH_MAX], path[PATH_MAX + 64], value[256];
	const char *command = argc > 1 ? argv[1] : "status";
	const char *testroot = getenv("MU300_SYSROOT");
	sysroot = testroot ? testroot : "";
	if (strlen(sysroot) > 1024) {
		fprintf(stderr, "mu300-usb: sysroot path is too long\n");
		return 2;
	}
	if (!strcmp(command, "boot"))
		return 0; /* All hardware boot policy is now in the kernel. */
	int native = !native_path(charger, sizeof(charger));
	if (strcmp(command, "status") && strcmp(command, "host") &&
	    strcmp(command, "host-external") && strcmp(command, "auto") && strcmp(command, "device")) {
		fprintf(stderr, "usage: mu300-usb [status|auto|host|device]\n");
		return 2;
	}
	if (!strcmp(command, "status")) {
		if (native)
			snprintf(path, sizeof(path), "%s/data_role", charger);
		else if (role_path(path, sizeof(path)))
			path[0] = 0;
		printf("role: %s\n", path[0] && !read_value(path, value, sizeof(value)) ? value : "unavailable");
		if (native) {
			snprintf(path, sizeof(path), "%s/host_policy", charger);
			printf("policy: %s\n", !read_value(path, value, sizeof(value)) ? value : "unavailable");
			snprintf(path, sizeof(path), "%s/otg_boost", charger);
			printf("5 V out (OTG boost): %s\n", !read_value(path, value, sizeof(value)) ?
			       !strcmp(value, "0") ? "off" : "on" : "unavailable");
			snprintf(path, sizeof(path), "%s/input_power_good", charger);
			printf("external input: %s\n", !read_value(path, value, sizeof(value)) ? value : "unavailable");
			snprintf(path, sizeof(path), "%s/sys/class/power_supply/sgm41511-charger/status", sysroot);
			printf("charger: %s\n", !read_value(path, value, sizeof(value)) ? value : "unavailable");
		} else {
			puts("native power driver: not bound (Host disabled)");
		}
		devices();
		return 0;
	}
	if (geteuid() != 0) {
		fprintf(stderr, "mu300-usb: run as root\n");
		return 1;
	}
	int automatic = !strcmp(command, "auto");
	int host = strcmp(command, "device") != 0;
	if (host && !native) {
		fprintf(stderr, "mu300-usb: native SGM41511 driver not bound; refusing Host\n");
		return 1;
	}
	if (native)
		snprintf(path, sizeof(path), "%s/data_role", charger);
	else if (role_path(path, sizeof(path))) {
		fprintf(stderr, "mu300-usb: no unique USB role switch\n");
		return 1;
	}
	FILE *file = fopen(path, "w");
	if (!file) {
		perror("mu300-usb: cannot open kernel role control");
		return 1;
	}
	int write_failed = fprintf(file, "%s\n", automatic ? "auto" : host ? "host" : "device") < 0;
	int close_failed = fclose(file) != 0;
	if (write_failed || close_failed) {
		perror("mu300-usb: kernel refused role request (Host requires external power)");
		return 1;
	}
	if (automatic) {
		puts("Automatic external-powered Host policy enabled; inspect status after enumeration");
		return 0;
	}
	struct timespec delay = {.tv_nsec = 100000000};
	for (int i = 0; i < 30; i++) {
		if (!read_value(path, value, sizeof(value)) && !strcmp(value, host ? "host" : "device")) {
			printf("USB %s selected; power policy is kernel-managed\n", host ? "Host" : "Device");
			return 0;
		}
		nanosleep(&delay, NULL);
	}
	fprintf(stderr, "mu300-usb: role transition was not confirmed; check dmesg\n");
	return 1;
}
