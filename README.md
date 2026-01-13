# MySubaru Websocket Home Assistant integration (stub)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Validate](https://github.com/alex-savin/hassio-integration-mysubaru/actions/workflows/validate.yml/badge.svg)](https://github.com/alex-savin/hassio-integration-mysubaru/actions/workflows/validate.yml)

Listener that keeps the latest payload from the add-on websocket stream, fires `mysubaru_updated` events, and exposes basic sensors and binary sensors per vehicle.

## Prerequisites

This integration requires the **[MySubaru Websocket Add-on](https://github.com/alex-savin/hassio-apps/tree/main/mysubaru-ws)** to be installed and running.

The add-on connects to MySubaru's servers, authenticates with your credentials, and exposes vehicle status via a local websocket. This integration then connects to that websocket to create Home Assistant entities.

### Add-on Installation

1. Add the add-on repository to Home Assistant:
   - Go to **Settings → Add-ons → Add-on Store**
   - Click the three dots (⋮) in the top right → **Repositories**
   - Add: `https://github.com/alex-savin/hassio-apps`
2. Find and install **MySubaru Websocket**
3. Configure the add-on with your MySubaru credentials
4. Start the add-on
5. Note the websocket URL (typically `ws://homeassistant.local:8080/ws` or `ws://localhost:8080/ws`)

For detailed add-on configuration and endpoints, see the [add-on documentation](https://github.com/alex-savin/hassio-apps/tree/main/mysubaru-ws).

## Install

### HACS (Recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=alex-savin&repository=hassio-integration-mysubaru&category=integration)

1. Open HACS in Home Assistant
2. Click "Integrations"
3. Click the three dots in the top right corner
4. Select "Custom repositories"
5. Add this repository URL: `https://github.com/alex-savin/hassio-integration-mysubaru`
6. Select "Integration" as the category
7. Click "Add"
8. Search for "MySubaru" and install it
9. Restart Home Assistant

### Manual Installation
1. Copy `custom_components/mysubaru` into your Home Assistant config directory.
2. Restart Home Assistant
3. In Settings → Devices & Services, click "Add Integration", search for "MySubaru", enter the websocket URL and your MySubaru credentials (username/password/PIN/device id/name/region). If the server reports the device is unregistered, a verification code will be sent and the flow will prompt for it.
4. Sensors/binary sensors will appear per vehicle once a payload is received. Listen for the `mysubaru_updated` event to inspect raw payloads.

### Entities (per vehicle)
- Sensors: odometer miles, fuel level %, range miles, average MPG, EV state-of-charge %, EV range miles, engine state.
- Binary sensors: doors open, windows open, locked.
 - Locks: aggregated door locks with remote lock/unlock.
 - Device tracker: GPS location/heading.
- Climate: status-only HVAC mode/action based on ignition state.

## Automation examples

#### Remind if doors are open after you arrive home
```yaml
alias: Subaru door reminder at home
trigger:
	- platform: state
		entity_id: binary_sensor.your_car_doors_open
		to: 'on'
		for: '00:05:00'
condition:
	- condition: state
		entity_id: device_tracker.your_car_location
		state: 'home'
action:
	- service: notify.mobile_app_your_phone
		data:
			title: "Subaru doors open"
			message: "{{ state_attr('binary_sensor.your_car_doors_open', 'friendly_name') }} still shows doors open."
``` 

#### Remind if any doors/windows are open when locking up for the night
```yaml
alias: Subaru close up reminder
trigger:
	- platform: time
		at: '22:30:00'
condition:
	- condition: or
		conditions:
			- condition: state
				entity_id: binary_sensor.your_car_doors_open
				state: 'on'
			- condition: state
				entity_id: binary_sensor.your_car_windows_open
				state: 'on'
action:
	- service: persistent_notification.create
		data:
			title: "Subaru not secured"
			message: >-
				Doors: {{ states('binary_sensor.your_car_doors_open') }},
				Windows: {{ states('binary_sensor.your_car_windows_open') }}.
				Please close and lock the vehicle.
``` 

### Notify on new Subaru trouble codes
```yaml
alias: Subaru trouble alert
trigger:
  - platform: event
    event_type: mysubaru_trouble
    event_data:
      event: added
action:
  - service: notify.mobile_app_your_phone
    data:
      title: "⚠️ {{ trigger.event.data.vehicle_name }} Trouble Detected"
      message: "{{ trigger.event.data.code }}: {{ trigger.event.data.description }}"
      data:
        tag: "subaru-trouble-{{ trigger.event.data.code }}"
        actions:
          - action: URI
            title: "View Details"
            uri: "/lovelace/vehicles"
```

### Notify when trouble codes are cleared
```yaml
alias: Subaru trouble cleared
trigger:
  - platform: event
    event_type: mysubaru_trouble
    event_data:
      event: cleared
action:
  - service: notify.mobile_app_your_phone
    data:
      title: "✅ {{ trigger.event.data.vehicle_name }} Trouble Cleared"
      message: "{{ trigger.event.data.code }}: {{ trigger.event.data.description }} has been resolved"
      data:
        tag: "subaru-trouble-{{ trigger.event.data.code }}"
```

### Auto-lock car if unlocked for more than 5 minutes
```yaml
alias: Subaru auto-lock after 5 minutes
description: Automatically locks the car if it remains unlocked for 5 minutes
trigger:
  - platform: state
    entity_id: lock.your_car_locks
    to: 'unlocked'
    for: '00:05:00'
condition:
  # Optional: only auto-lock when at home
  - condition: state
    entity_id: device_tracker.your_car_location
    state: 'home'
  # Optional: don't lock if engine is running
  - condition: state
    entity_id: sensor.your_car_engine_state
    state: 'off'
action:
  - service: lock.lock
    target:
      entity_id: lock.your_car_locks
  - service: notify.mobile_app_your_phone
    data:
      title: "🔒 Subaru Auto-Locked"
      message: "Your car was unlocked for 5 minutes and has been automatically locked."
mode: single
```

### Log all trouble events to the logbook
```yaml
alias: Log Subaru trouble
trigger:
  - platform: event
    event_type: mysubaru_trouble
action:
  - service: logbook.log
    data:
      name: "{{ trigger.event.data.vehicle_name }}"
      message: "{{ trigger.event.data.event }} trouble: {{ trigger.event.data.code }}: {{ trigger.event.data.description }}"
```

### Send Telegram notification on trouble codes

The Telegram bot integration creates a notify entity for each allowed chat ID. Use `notify.send_message` with your entity:

```yaml
alias: Subaru Telegram trouble alert
description: Send a Telegram message when a new trouble code is detected
trigger:
  - platform: event
    event_type: mysubaru_trouble
    event_data:
      event: added
action:
  - action: notify.send_message
    target:
      entity_id: notify.telegram_your_chat  # Replace with your Telegram notify entity
    data:
      title: "⚠️ {{ trigger.event.data.vehicle_name }} Trouble Detected"
      message: |
        *New trouble code detected*
        🚗 Vehicle: {{ trigger.event.data.vehicle_name }}
        🔧 Code: `{{ trigger.event.data.code }}`
        📝 Description: {{ trigger.event.data.description }}
      data:
        message_thread_id: 123  # Optional: send to a specific topic/thread in a group
```

### Send Telegram notification with location when car moves
```yaml
alias: Subaru Telegram location update
description: Send a Telegram message with car location when it moves significantly
trigger:
  - platform: state
    entity_id: device_tracker.your_car_location
condition:
  - condition: template
    value_template: "{{ trigger.from_state.state != trigger.to_state.state }}"
action:
  - action: telegram_bot.send_location
    data:
      latitude: "{{ state_attr('device_tracker.your_car_location', 'latitude') }}"
      longitude: "{{ state_attr('device_tracker.your_car_location', 'longitude') }}"
      message_thread_id: 123  # Optional: send to a specific topic/thread
  - action: notify.send_message
    target:
      entity_id: notify.telegram_your_chat  # Replace with your Telegram notify entity
    data:
      message: |
        🚗 *{{ state_attr('device_tracker.your_car_location', 'friendly_name') }}* moved
        📍 New location: {{ states('device_tracker.your_car_location') }}
        🔋 Fuel: {{ states('sensor.your_car_fuel_level') }}%
        📏 Odometer: {{ states('sensor.your_car_odometer') }} mi
      data:
        message_thread_id: 123  # Optional: send to a specific topic/thread
mode: single
```

### Daily Telegram status summary
```yaml
alias: Subaru daily Telegram summary
description: Send a daily summary of your car's status via Telegram
trigger:
  - platform: time
    at: "08:00:00"
action:
  - action: notify.send_message
    target:
      entity_id: notify.telegram_your_chat  # Replace with your Telegram notify entity
    data:
      title: "🚗 Daily Subaru Status"
      message: |
        *Good morning! Here's your car status:*
        
        🔐 Locked: {{ states('lock.your_car_locks') }}
        🚪 Doors: {{ 'Open' if is_state('binary_sensor.your_car_doors_open', 'on') else 'Closed' }}
        🪟 Windows: {{ 'Open' if is_state('binary_sensor.your_car_windows_open', 'on') else 'Closed' }}
        ⛽ Fuel: {{ states('sensor.your_car_fuel_level') }}%
        📏 Range: {{ states('sensor.your_car_range') }} mi
        📊 Avg MPG: {{ states('sensor.your_car_avg_mpg') }}
        📍 Location: {{ states('device_tracker.your_car_location') }}
      data:
        message_thread_id: 123  # Optional: send to a specific topic/thread
mode: single
```

### Voice assistant prompt to lock unlocked vehicle

Use Home Assistant Voice PE (Assist) to ask the user if they want to lock the car when it's been unlocked for 5 minutes:

```yaml
- alias: Subaru voice lock reminder
  description: Ask user via voice assistant to lock the car if unlocked for 5 minutes
  trigger:
    - platform: state
      entity_id: lock.your_car_locks
      to: "unlocked"
      for: "00:05:00"
  condition:
    # Only prompt when at home
    - condition: state
      entity_id: device_tracker.your_car_location
      state: "home"
    # Don't prompt if engine is running
    - condition: state
      entity_id: sensor.your_car_engine_state
      state: "off"
  action:
    - alias: "Ask user via Voice PE"
      action: assist_satellite.ask_question
      target:
        entity_id: assist_satellite.home_assistant_voice_pe  # Replace with your Voice PE entity
      data:
        question: "Your Subaru has been unlocked for 5 minutes. Would you like me to lock it?"
        answers:
          - id: "yes"
            sentences:
              - "[yes] [please]"
              - "yeah"
              - "sure"
              - "lock it"
              - "go ahead"
          - id: "no"
            sentences:
              - "no [thanks]"
              - "nope"
              - "not now"
              - "leave it"
              - "nevermind"
              - "cancel"
      response_variable: answer
    - choose:
        - conditions:
            - condition: template
              value_template: "{{ answer.id == 'yes' }}"
          sequence:
            - action: lock.lock
              target:
                entity_id: lock.your_car_locks
            - action: assist_satellite.announce
              target:
                entity_id: assist_satellite.home_assistant_voice_pe
              data:
                message: "Done. Your Subaru is now locked."
            - action: notify.mobile_app_your_phone
              data:
                title: "🔒 Subaru Locked"
                message: "Your car was locked via voice command."
      default:
        - action: assist_satellite.announce
          target:
            entity_id: assist_satellite.home_assistant_voice_pe
          data:
            message: "OK, I'll leave it unlocked."
  mode: single
```

## Notes
- Add-on config no longer collects credentials; the config flow sends them to the websocket server during setup.
- No entities are created yet; this is scaffolding to build sensors from streamed data.
- Reconnects automatically every 10 seconds on failure.
