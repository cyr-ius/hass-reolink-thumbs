# Reolink thumbs

This a _custom component_ for [Home Assistant](https://www.home-assistant.io/).

With Reolink-thumbs, this allows you to display thumbnails in the media sources section.

<img width="200" height="400" alt="image" src="https://github.com/user-attachments/assets/16134546-c388-47d4-adc7-46b26ae02a5b" />

Images are generated on the fly while you browse a day: the list of recordings
opens immediately and each thumbnail appears as soon as it is ready. At most
three recordings per Reolink device are read at the same time.

Thumbnails are stored in `/config/.cache/reolink_thumbs` (not included in
backups) and served only to logged-in users. Thumbnails created by earlier
versions in `/config/www/recordings` are moved there when they are displayed;
that folder was readable without authentication and can be deleted once you no
longer need it.

![GitHub release](https://img.shields.io/github/release/Cyr-ius/hass-reolink-thumbs)
[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg)](https://github.com/hacs/integration)
![downloads](https://img.shields.io/badge/dynamic/json?color=41BDF5&logo=home-assistant&label=integration%20usage&suffix=%20installs&cacheSeconds=15600&url=https://analytics.home-assistant.io/custom_integrations.json&query=$.reolink-thumbs.total)

## Installation

Add Reolink-thumbs module via HACS

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=cyr-ius&repository=hass-reolink-thumbs&category=integration)

Add your equipment via the Integration menu

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=hass-reolink-thumbs)

## External snapshots (optional)

By default a thumbnail is the first frame of the recording, which often shows
an empty scene because of the pre-recording. If another tool already saves
pictures of detected objects (for example [VisionState](https://github.com/oleost/VisionState),
Frigate or your own scripts), Reolink thumbs can use those pictures instead.
Object detection and cropping stay entirely in that external tool; this
integration only looks for image files.

Open **Settings → Devices & services → Reolink thumbs → Configure**:

- **Snapshot directory**: absolute path, empty = disabled (default). Placeholders:
  - `{camera}`: camera name as shown by the Reolink integration
  - `{camera_slug}`: the same name as a slug (`Front Door` → `front_door`)
  - `{channel}`: channel number
  - `{date}`: day of the snapshot, with an optional format such as `{date:%Y-%m-%d}`
- **File name pattern** (default `Snapshot_%Y%m%d_%H%M%S*`): matched against
  the whole file name. Supported: `%Y %y %m %d %H %M %S %f` (local time in the
  Home Assistant time zone), `%s` (Unix timestamp), `%%`, and the wildcards `*`
  and `?`. Example: `Snapshot_%Y%m%d_%H%M%S_person*` only uses pictures of persons.
- **Tolerance** (seconds, default 0): also accept snapshots taken shortly before
  the start or after the end of a recording.

When a thumbnail is missing, the integration searches the directory for a
snapshot whose timestamp lies within the recording (start − tolerance to
end + tolerance). If several snapshots match, the earliest one is used. It is
scaled to 256 px and stored as PNG like any other thumbnail. Without a match the
first video frame is used as before. Existing thumbnails are not replaced:
delete them from `/config/.cache/reolink_thumbs` to regenerate.

Recording times come from the camera (including its time zone and daylight
saving time). Local timestamps in file names are interpreted in the Home
Assistant time zone; during the repeated hour when daylight saving time ends,
the first occurrence is assumed. Use `%s` to avoid this ambiguity.

### Example: VisionState

Save every new snapshot of a VisionState image entity into one folder per
camera and day:

```yaml
alias: "Save VisionState snapshots"
triggers:
  - trigger: state
    entity_id: image.front_door_person_snapshot
    # Only new images, not the access token rotation (attribute change)
    to: null
    not_from: unavailable
conditions:
  - condition: template
    value_template: "{{ trigger.to_state.state not in ['unknown', 'unavailable'] }}"
actions:
  - action: image.snapshot
    target:
      entity_id: "{{ trigger.entity_id }}"
    data:
      filename: >-
        /media/snapshots/front_door/{{ now().strftime('%Y-%m-%d') }}/Snapshot_{{
        now().strftime('%Y%m%d_%H%M%S') }}_person.jpg
mode: queued
```

Options:

- Snapshot directory: `/media/snapshots/{camera_slug}/{date:%Y-%m-%d}`
- File name pattern: `Snapshot_%Y%m%d_%H%M%S_person*`

If your folders are named differently from the cameras, create a symbolic link
per camera, e.g. `ln -s /media/snapshots/backyard /media/snapshots/front_door`.

Remember to delete old snapshots, for example with a `shell_command` that runs
`find /media/snapshots -type f -mtime +14 -delete` once a day.

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
      minutes: 2 # Wait for recording to be saved
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
