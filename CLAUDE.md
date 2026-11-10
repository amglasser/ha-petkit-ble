# CLAUDE.md

**Claude Code Instructions for PetKit BLE Integration**

This file provides comprehensive guidance for Claude Code (claude.ai/code) when working with this repository. Follow these instructions exactly to maintain consistency and quality.

## Project Overview

PetkitW5BLEMQTT is a Python library for controlling Petkit W5 series water fountains via Bluetooth Low Energy (BLE). The library enables local control without cloud dependencies.

## Core Architecture

### Main Components

- **Manager** (`main.py`): Orchestrates the entire system, handles argument parsing, and coordinates BLE operations
- **BLEManager** (`ble_manager.py`): Manages Bluetooth Low Energy connections, device scanning, and message queuing
- **Device** (`device.py`): State management with status, config, and info properties; handles device attributes and settings
- **Commands** (`commands.py`): Device command interface for initialization, control operations, and data retrieval
- **EventHandlers** (`event_handlers.py`): Processes BLE notifications and updates device state
- **Parsers** (`parsers.py`): Data parsing utilities for BLE message interpretation
- **Utils** (`utils.py`): Utility functions for data processing and conversion

### Key Design Patterns

**Async/Await Architecture**: All BLE operations are asynchronous using asyncio
**State Management**: Device state is centralized in the Device class with property-based access
**Message Queuing**: BLE operations use an asyncio.Queue for ordered message processing
**Event-Driven**: Uses event handlers to process BLE notifications and update device state
**Dependency Injection**: Components receive their dependencies through constructor parameters

### Communication Flow

1. BLEManager scans for Petkit devices (W4, W5, CTW2 series)
2. Manager establishes BLE connection and initializes device
3. Commands class handles device initialization and data retrieval
4. EventHandlers processes incoming BLE notifications and updates device state
5. Continuous heartbeat maintains BLE connection

## Development Commands

### Running the Application

```bash
# Basic BLE operation
python main.py --address "A1:B2:C3:D4:E5:F6"

# With debug logging
python main.py --address "A1:B2:C3:D4:E5:F6" --logging_level "DEBUG"
```

### Installing Dependencies

```bash
# Install required packages
pip install -r PetkitW5BLEMQTT/requirements.txt

# Or install specific dependencies
pip install bleak>=1.0.1
```

## Release Management

### 🚀 Automated Release Process

**CRITICAL**: Always use the automated release script (`./release.sh`) for ALL releases. Never manually edit version numbers in `manifest.json` - this is handled automatically by the GitHub workflow.

#### 📋 Quick Reference

```bash
# Most common usage (interactive)
./release.sh                    # Patch version increment with prompts
./release.sh minor              # Minor version increment with prompts

# Automated usage (no prompts - ideal for scripts)
./release.sh -y                                 # Quick patch release
./release.sh -y minor "Add device support"      # Minor release with message
./release.sh --yes major "Breaking changes"     # Major release with message

# Testing (dry run)
./release.sh --dry-run minor                    # See what would happen
./release.sh -n -y v1.0.0                      # Test specific version

# Custom versions
./release.sh -y v2.1.0 "Stable release"        # Set specific version
./release.sh -y 1.0.0                          # Version without 'v' prefix

# Help
./release.sh --help                            # Show detailed usage
```

#### 🎯 Semantic Versioning Guide

| Version Type | When to Use | Example | Command |
|--------------|-------------|---------|----------|
| **Patch** (0.0.x) | Bug fixes, small improvements | 0.1.38 → 0.1.39 | `./release.sh` or `./release.sh patch` |
| **Minor** (0.x.0) | New features, enhancements | 0.1.38 → 0.2.0 | `./release.sh minor` |
| **Major** (x.0.0) | Breaking changes, major refactors | 0.1.38 → 1.0.0 | `./release.sh major` |
| **Custom** | Specific version requirement | 0.1.38 → 2.5.1 | `./release.sh v2.5.1` |

#### ⚙️ What the Script Does Automatically

1. **🔍 Environment Validation**
   - Verifies git repository and required files
   - Checks for required commands (git, python3)
   - Validates manifest.json exists

2. **🔄 Remote Synchronization** 
   - Fetches latest changes from remote
   - Automatically pulls and rebases if needed
   - Stashes/restores local changes safely

3. **📦 Version Management**
   - Increments version according to semantic versioning
   - Validates version doesn't already exist
   - Prevents duplicate or invalid versions

4. **💾 Git Operations**
   - Stages ALL uncommitted changes
   - Creates semantic commit messages
   - Pushes commits to main branch
   - Creates and pushes annotated version tags with automatic conflict resolution

5. **🏷️ Smart Tag Management**
   - Automatically detects existing local and remote tags
   - Cleans up conflicting tags without user intervention
   - Provides clear feedback on tag operations
   - Ensures clean tag creation every time

6. **🤖 Workflow Automation**
   - Triggers GitHub Actions workflow
   - Updates manifest.json automatically
   - Generates AI-powered release notes
   - Creates downloadable GitHub release

#### 🛡️ Built-in Safety Features

- **Comprehensive error messages** with suggested fixes
- **Dry-run mode** to test changes without executing
- **Automatic conflict resolution** with stash/restore
- **Smart tag cleanup** automatically handles existing tag conflicts
- **Version validation** to prevent duplicates
- **Recovery instructions** when operations fail
- **Environment checks** before starting
- **Graceful failure** with cleanup

### ⚠️ Manual Release Commands (EMERGENCY ONLY)

**WARNING**: Only use manual commands if the release script completely fails and cannot be fixed.

```bash
# EMERGENCY manual process (avoid if possible)
git add -A
git commit -m "chore: emergency release - v0.1.41"
git push origin main
git tag -a v0.1.41 -m "Emergency release v0.1.41"
git push origin v0.1.41
# Note: This will NOT update manifest.json automatically
```

#### 🔧 Troubleshooting Release Script

If the release script fails, try these steps:

```bash
# 1. Check environment and permissions
./release.sh --help                    # Verify script works
git status                              # Check repository state
git remote -v                          # Verify remote access

# 2. Test with dry run
./release.sh --dry-run patch           # Test without changes

# 3. Manual git operations if needed
git fetch origin main                  # Update remote info
git pull --rebase origin main          # Sync with remote
git push origin main                   # Test push access

# 4. Reset if stuck
git stash                              # Save local changes
git reset --hard origin/main           # Reset to remote state
git stash pop                          # Restore changes
```

#### 📚 Additional Resources

- **Detailed Guide**: See [RELEASE_AUTOMATION.md](RELEASE_AUTOMATION.md)
- **Testing Guide**: See [.github/RELEASE_TESTING.md](.github/RELEASE_TESTING.md)
- **Workflow Details**: See [.github/workflows/release.yml](.github/workflows/release.yml)

### System Service Setup

The project includes a systemd service file (`petkitW5BLEMQTT.service`) for running as a system daemon. Note: This service file contains outdated MQTT parameters and should be updated for BLE-only operation.

## Key Technical Details

### BLE Communication

- Uses UUIDs from Constants class for read/write operations
- Implements custom message queuing for reliable BLE communication
- Handles device discovery by filtering for Petkit device names
- Maintains persistent connection with heartbeat mechanism

### Device State Management

Device class manages three categories of state:
- **Status**: Real-time device metrics (battery, warnings, runtime, etc.)
- **Config**: Device settings (smart mode timings, LED settings, DND settings)
- **Info**: Device information (firmware, serial, product name)

### Filter reset (new filter installed)

- Service `petkit_ble.reset_filter` (see `services.yaml`) sends command 222 with data `[0]`, which resets the filter life indicator on the fountain. It can be called from Developer Tools → Actions, a dashboard button or an automation.
- Written for the W5. **Not yet verified on the CTW3** (as of 0.4.3). After the first real filter change, check that `sensor.water_fountain_filter_remaining` returns to ~100%.
- CTW3 payloads are decoded since 0.4.4 (cmd 210 = 30 bytes, cmd 211 = 12 bytes, cmd 230 = both; see the layout comment in `parsers.py`). The CTW3 write characteristic offers write and write-without-response; the CTW3 rejects write-with-response with ATT "Unlikely Error", so the adapter prefers write-without-response.
- The CTW3 is identified from its advertisement as *manufacturer* data (company id 0xC1A4, payload `38 A5 76 82 00 F7`, model byte = payload[5] = 247), not service data; the name `Petkit_CTW3_100` is the fallback. Until 0.4.8 only service data was read, so the CTW3 was decoded with the W5 layout. The test fixture must advertise like a real unit (manufacturer data only).
- Host Bluetooth: connects timing out with "BlueZ: no device object" while the Mac inspector connects fine pointed at the HA host's Realtek RTL8821CE radio (0bda:c821), which only recovered after a host reboot. A BLED112 (Bluegiga BGAPI) cannot be used by HA; use an HCI dongle (e.g. ASUS USB-BT500) or an ESPHome Bluetooth proxy.
- CTW3 mode command (220) payload is `[power, suspend, mode]`. `set_device_config` is refused on the CTW3 until its 12 byte settings layout is implemented.
- The fountain reports its firmware version (command 200) but there is no known BLE way to check for or install firmware updates. That is done through the Petkit phone app and Petkit's cloud; do not try to implement it without a published protocol, because a bad transfer could brick the device. After a firmware update the fountain may need a power cycle before Home Assistant can reconnect.

### Data Processing

- Processes BLE notifications through EventHandlers
- Updates device state automatically based on parsed data
- Maintains device status, configuration, and info properties
- Supports device control through Commands interface

### Error Handling & Reconnection

- **Immediate reconnection** on BLE disconnect (100ms initial delay)
- **Progressive retry delays**: 100ms → 500ms → 1s → 5s for optimal recovery
- **Continuous retry attempts** until connection is restored
- **Connection state tracking** with detailed logging
- **Graceful cleanup** on KeyboardInterrupt
- **Automatic recovery** from temporary connection failures
- **Enhanced logging** with emojis for better visibility (🔄, ✅, ⚠️, ❌)

## Important Notes & Limitations

### ⚠️ Current Limitations
- **Device compatibility**: Only tested with Petkit Eversweet 2 Solo
- **Device ID/secret**: Uses hardcoded values (device-specific implementation needed)
- **Scheduling features**: Do Not Disturb and Lights Out scheduling not yet supported
- **App interference**: Using CMD 73 may interfere with official Petkit app communication

### ✅ What Works Well
- **BLE-only operation**: No cloud dependencies, fully local control
- **Immediate reconnection**: Auto-reconnects within 100ms of disconnect
- **Home Assistant integration**: Full HA entity and service support
- **Real-time updates**: Live status monitoring and control
- **Automated releases**: Complete CI/CD pipeline with version management

### 🔄 Migration Notes
- **MQTT removed**: Library now provides BLE-only control
- **Legacy service files**: systemd service file contains outdated MQTT parameters
- **Version management**: Always use `./release.sh` - never edit manifest.json manually

## Project Structure

### 📁 Core Files
```
├── custom_components/petkit_ble/     # Home Assistant integration
│   ├── __init__.py                   # Integration setup
│   ├── manifest.json                 # Integration metadata (auto-updated)
│   ├── coordinator.py                # Data coordinator with reconnection
│   ├── ha_bluetooth_adapter.py       # HA Bluetooth integration
│   └── [sensors, switches, etc.]     # HA entity implementations
├── PetkitW5BLEMQTT/                  # Core BLE library
│   ├── main.py                       # Entry point and orchestrator
│   ├── ble_manager.py                # BLE connection management
│   ├── device.py                     # Device state management
│   ├── commands.py                   # Device command interface
│   ├── event_handlers.py             # BLE notification processing
│   └── [parsers, utils, constants]   # Support modules
└── release.sh                        # Automated release script
```

### 🔧 Release & CI/CD Files
```
├── .github/workflows/release.yml     # GitHub Actions workflow
├── .github/update-manifest-version.py# Version update script
├── .github/test-*.sh                 # Testing scripts
├── release.sh                        # Main release automation
├── RELEASE_AUTOMATION.md             # Release guide
└── CLAUDE.md                         # This file - Claude instructions
```

### 📦 Dependencies
- **Minimal runtime**: `bleak>=1.0.1`, `bleak-retry-connector>=3.0.0`
- **Development**: Standard Python tools (git, python3)
- **Testing**: Optional `act` for local workflow testing
- **No test suite**: Currently manual testing only