# Reolink thumbs

This a _custom component_ for [Home Assistant](https://www.home-assistant.io/).

With Reolink-thumbs, this allows you to display thumbnails in the media sources section.

<img width="200" height="400" alt="image" src="https://github.com/user-attachments/assets/16134546-c388-47d4-adc7-46b26ae02a5b" />

Images are generated on the fly during a day's viewing. They are stored in the `/config/www/recordings` directory.

![GitHub release](https://img.shields.io/github/release/Cyr-ius/hass-reolink-thumbs)
[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg)](https://github.com/hacs/integration)

## Installation

Add Reolink-thumbs module via HACS

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=cyr-ius&repository=hass-reolink-thumbs&category=integration)

Add your equipment via the Integration menu

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=hass-reolink-thumbs)

## Service

### `reolink_thumbs.generate_thumbnails`

Generate thumbnails for Reolink camera recordings. This service can be called manually or triggered via automations.

**Parameters:**
- `days` (optional, default: 1): Number of days to look back (1-365)

**Examples:**

```yaml
# Generate thumbnails for today only (default)
service: reolink_thumbs.generate_thumbnails

# Generate thumbnails for last 7 days
service: reolink_thumbs.generate_thumbnails
data:
  days: 7

# Generate thumbnails for last 30 days
service: reolink_thumbs.generate_thumbnails
data:
  days: 30
```

### Automation Examples

**Generate thumbnails every 30 minutes:**
```yaml
alias: "Reolink: Auto-Generate Thumbnails"
trigger:
  - platform: time_pattern
    minutes: "/30"
action:
  - service: reolink_thumbs.generate_thumbnails
    data:
      days: 1
mode: single
```

**Generate thumbnails after motion detection:**
```yaml
alias: "Reolink: Thumbnails after Motion"
trigger:
  - platform: state
    entity_id: binary_sensor.reolink_camera_motion
    to: "on"
action:
  - delay:
      minutes: 2  # Wait for recording to be saved
  - service: reolink_thumbs.generate_thumbnails
    data:
      days: 1
mode: restart
```

## Troubleshooting

### Enable Debug Logging

To enable detailed debug logging for troubleshooting, add the following to your `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.reolink_thumbs: debug
```

After adding this configuration, restart Home Assistant. Debug logs will help identify issues with:
- Thumbnail generation failures
- Camera connection problems
- Service call issues
- FFmpeg errors

### Common Issues

**Thumbnails not generating:**
- Check that cameras have recordings on the SD card
- Verify FFmpeg is installed (required for thumbnail generation)
- Check logs for error messages with debug logging enabled

**Service not appearing:**
- Restart Home Assistant after installation
- Check that the integration is properly installed via HACS
- Verify `services.yaml` exists in the component directory

**Cameras showing as offline:**
- These will be logged as DEBUG messages (not errors)
- Ensure cameras are powered on and connected to the network
- Check Reolink integration is working correctly
