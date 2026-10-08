/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef U30_ANDROID_CHARGE_POLICY_H
#define U30_ANDROID_CHARGE_POLICY_H

struct u30_jeita_row { int temp, recovery; unsigned int charge_ua, voltage; };
struct u30_jeita_state { int zone, previous_temp, initialized; };

struct u30_charge_profile { unsigned int charge_ua, input; struct u30_jeita_row rows[5]; };
struct u30_charge_sample { int source, temp, valid_temp; unsigned int contract; };

/* Same threshold/recovery ordering as the U30 vendor charger-manager. */
static inline int u30_jeita_zone(const struct u30_jeita_row *rows, int count,
                                struct u30_jeita_state *state, int temp)
{
    int i, status, recovery, zone = state->zone;
    for (i = count - 1; i >= 0; --i)
        if ((i && temp >= rows[i].temp) || (!i && temp > rows[i].temp))
            break;
    status = i + 1;
    if (!status || status == count) {
        zone = status;
    } else {
        for (i = count - 1; i >= 0; --i)
            if ((i && temp >= rows[i].recovery) || (!i && temp > rows[i].recovery))
                break;
        recovery = i + 1;
        if (!state->initialized)
            zone = 0;
        if (state->initialized && state->previous_temp > temp) {
            if (recovery == count || rows[recovery].temp > rows[recovery].recovery) {
                if (zone >= recovery) zone = recovery;
            } else if (zone >= status) zone = status;
        } else {
            if (recovery == count) {
                if (zone <= status) zone = status;
            } else if (rows[recovery].recovery < rows[recovery].temp) {
                if (zone <= recovery) zone = recovery;
            } else if (zone <= status) zone = status;
        }
    }
    state->zone = zone;
    state->previous_temp = temp;
    state->initialized = 1;
    return zone;
}

/* Read-only UMP9620 BC1.2 status; reject unfinished/ambiguous detection. */
static inline int u30_bc_source(unsigned int status)
{
    if ((status & 0x804) != 0x804) return 0;
    switch (status & 0xe0) {
    case 0x80: return 1; /* SDP */
    case 0x40: return 2; /* DCP */
    case 0x20: return 3; /* CDP */
    default: return 0;
    }
}

/* Round down and skip the special 4.352 V code; never exceed stock max. */
static inline unsigned int u30_voltage_code(unsigned int uv)
{
    unsigned int code = uv <= 3856000 ? 0 : (uv - 3856000) / 32000;
    if (code > 24) code = 24;
    if (code == 15 && uv < 4352000) code = 14;
    return code;
}
#endif
