# Capacity correction under development

This directory is isolated from deployed native13 sources and release archives. It is not a firmware release and has not been flashed.

## Confirmed arithmetic defect and correction

The deployed candidate formula adds measured net charge to residual charge estimated using the old model. That preserves a fraction of the old model error. With a genuinely qualified10% low reference, a3000mAh cell accepts2700mAh net charge to Full; the old3847mAh model estimates a3085mAh candidate instead of3000.

The development formula infers total capacity as measured net uAh divided by the charged fraction `(1000 - start_cap_tenth)/1000`, then rounds once to mAh. Previous model remains a sanity and continuity check, not residual energy. All genuine low-reference, actual counter, duration, discontinuity and protected Full requirements remain mandatory.

WSL GCC `-std=c11 -Wall -Wextra -Werror` policy and state tests passed on2026-10-05. Tests include independence from every sampled valid previous model1000..6000mAh, all low fractions1..10%, rounding boundaries, exact-design case, overflow/range/duration guards,1440tick full cycle with counter wrap, signed discharge, and all existing continuity faults. These are algorithm tests, not independent device-capacity evidence.

## Remaining physical qualification problem

At externally unpowered idle the router draws roughly0.5..0.7A; after input restoration it immediately charges at~1.5A. Neither provides8genuine buffered samples within +/-50mA. Native13 FCC state remains inactive/sequence0. Relaxing this gate or creating a learned record would invent qualification.

SGMICRO SGM41511 August2024 datasheet: pages18/23 describe system power path; page33 documents CHG_CONFIG independently of SYS_MIN. Input supply can power SYS while battery charging is disabled, but supplement mode can still draw battery current under insufficient input. Therefore actual battery-current qualification remains essential.

Primary reference: https://www.sg-micro.com/rect/assets/d5875e35-e037-4d49-9050-ad61cb59692c/SGM41511.pdf

A bounded quiet-window decision component is now implemented in source/fcc-quiet-policy.h and its tests pass; kernel/charger integration and real hardware proof remain absent. The component requires actual inhibit readback, real8-buffer low references,180seconds of continuous qualifying samples with a5mV OCV band, and a600second absolute timeout. These engineering thresholds still require physical relaxation validation; they are not independently proven battery-chemistry constants. The charger adapter must independently enforce the absolute timeout even if the FGU worker stops. It must keep external input and OTG boost-off requirements, preserve SYS/BATFET/hardware safety protections, coordinate the charger monitor that otherwise re-enables charging, restore ordinary Android charging on timeout/fault/disconnection, and expose actual low-reference evidence. FGU/charger lock ordering, PM and teardown must be checked. Real quiet buffered samples and stable relaxed voltage are necessary; a nominal wait alone is insufficient. Do not toggle live registers ad hoc during the current charge cycle.

## Remaining delivery and acceptance work

Integrate the correction and a tested quiet-window coordinator into an isolated complete kernel/firmware, build all matching modules and rollback-capable release, perform device preflight and installation after the current charge cycle. Then prove qualified low-to-full learning, persistent learned record and pure OpenWrt reboot restore, independent validation cycle, real100%/Full charge termination and no false restart jump. Retain the latest usable recovery backup after each verified deployment. None of these hardware requirements is proven by the isolated tests above.

## Charger adapter progress at2026-10-05 22:30

Staged source/sgm41511-native.c now implements standard CHARGE_BEHAVIOUR AUTO/INHIBIT_CHARGE, actual charge-bit readback, a charger-owned600second boottime lease, one lease per readable input attachment, no extension on repeated requests, and cancellation on source/read/temperature/fault loss. Scheduling is serialized with stop under the charger lock. Only CHG_CONFIG changes; SYS/BATFET/OTG remain untouched. AUTO releases ownership and lets existing Android/JEITA policy decide charging. Initial hardware-Full rearm is deferred during an active quiet lease.

Actual setter/expiry functions extracted by test-charger-quiet.py passed faulting I2C tests for lease renewal attempts, expiry without FGU callbacks, readable versus unreadable detach, seven expiry/fault scenarios, all six read points, failed writes/stuck readback, six inadmissible starts, and stopping. An initial misleading-indentation warning in the test mock was corrected; final tests compiled with -Werror and passed.

compile-adapters.py uses the pinned native13 kernel command and headers, removes dependency output into the old build, and writes object/log/manifest only here. Current staged charger and FGU objects compile successfully. This is object compatibility proof, not a linked native14 kernel/module build. The staged FGU still has its native13 coordination logic and does NOT yet request/manage the new quiet interface. Complete FGU integration, full kernel/modules/firmware and all hardware learning/termination/persistence proof remain pending. Nothing in this directory was flashed.

## FGU coordinator and full build at2026-10-05 22:46

FGU now reads real input/health/inhibit status alongside battery low ADC/counter, requires the quiet policy ACCEPT_RELEASE before starting FCC, and starts from that same low reference/counter. Active learning is invalidated by readable input loss or source-read failures. Pending candidate commit also requires fresh readable powered/healthy input. Quiet diagnostics are appended to fcc_status; existing runtime record parser ignores unrecognized fields. Restored genuinely learned model stays intact.

Charger writes happen after unlocking FGU; reference is retained through the callback. Failed commands cancel quiet/learning and clear pending output. Stop waits for the worker then sends AUTO. Existing Full/current/temperature/dwell guards are retained. Actual coordinator, actual worker and ADC/commit/restore helpers passed strict local tests, including an entire1440tick cycle with counter wrap. Both staged real driver objects compile. The earlier statement that FGU integration is pending is superseded by this section; hardware integration remains unproven.

build-complete-kernel.py launched in a new independent /root/u30air-native14-build-20261005 tree. Kernel/project/build copies and olddefconfig completed, and full Image compilation is running in exec session58708. The script then builds/verifies31matching modules and creates a kernel bundle/input/output manifest. Source hashes are frozen at build start and verified after module build. Full firmware rootfs/runtime packaging and device preflight/deployment remain pending. Do not restart because an observation wait expires; inspect the actual process/session and logs first. No native14 hardware write has occurred.
