// GENERATED CODE - DO NOT MODIFY BY HAND
// Generated from contracts/schemas, contract 1.0.0.
// Regenerate with: python contracts/generate.py
//
// ignore_for_file: constant_identifier_names, prefer_null_aware_operators

// To parse this JSON data, do
//
//     final errorResponse = errorResponseFromJson(jsonString);
//     final healthStatus = healthStatusFromJson(jsonString);
//     final systemState = systemStateFromJson(jsonString);
//     final sensorsSnapshot = sensorsSnapshotFromJson(jsonString);
//     final deviceLink = deviceLinkFromJson(jsonString);
//     final environmentReading = environmentReadingFromJson(jsonString);
//     final presenceReading = presenceReadingFromJson(jsonString);
//     final occupancySnapshot = occupancySnapshotFromJson(jsonString);
//     final zoneOccupancy = zoneOccupancyFromJson(jsonString);
//     final lightingState = lightingStateFromJson(jsonString);
//     final lightingModeRequest = lightingModeRequestFromJson(jsonString);
//     final zoneLightRequest = zoneLightRequestFromJson(jsonString);
//     final lecture = lectureFromJson(jsonString);
//     final lectureCreate = lectureCreateFromJson(jsonString);
//     final lecturePatch = lecturePatchFromJson(jsonString);
//     final currentLecture = currentLectureFromJson(jsonString);
//     final lectureEvent = lectureEventFromJson(jsonString);
//     final climateState = climateStateFromJson(jsonString);
//     final climateTargetRequest = climateTargetRequestFromJson(jsonString);
//     final presentationState = presentationStateFromJson(jsonString);
//     final presentationRequest = presentationRequestFromJson(jsonString);
//     final announcement = announcementFromJson(jsonString);
//     final playAnnouncementRequest = playAnnouncementRequestFromJson(jsonString);
//     final announcementPlayback = announcementPlaybackFromJson(jsonString);
//     final decision = decisionFromJson(jsonString);
//     final settings = settingsFromJson(jsonString);
//     final settingsPatch = settingsPatchFromJson(jsonString);
//     final historySeries = historySeriesFromJson(jsonString);
//     final voiceAskRequest = voiceAskRequestFromJson(jsonString);
//     final voiceAskResponse = voiceAskResponseFromJson(jsonString);
//     final voiceCommand = voiceCommandFromJson(jsonString);
//     final voiceWake = voiceWakeFromJson(jsonString);
//     final voiceTranscript = voiceTranscriptFromJson(jsonString);
//     final heartbeat = heartbeatFromJson(jsonString);
//     final busEnvelope = busEnvelopeFromJson(jsonString);

import 'dart:convert';

const String contractVersion = "1.0.0";

ErrorResponse errorResponseFromJson(String str) => ErrorResponse.fromJson(json.decode(str));

String errorResponseToJson(ErrorResponse data) => json.encode(data.toJson());

HealthStatus healthStatusFromJson(String str) => HealthStatus.fromJson(json.decode(str));

String healthStatusToJson(HealthStatus data) => json.encode(data.toJson());

SystemState systemStateFromJson(String str) => SystemState.fromJson(json.decode(str));

String systemStateToJson(SystemState data) => json.encode(data.toJson());

SensorsSnapshot sensorsSnapshotFromJson(String str) => SensorsSnapshot.fromJson(json.decode(str));

String sensorsSnapshotToJson(SensorsSnapshot data) => json.encode(data.toJson());

DeviceLink deviceLinkFromJson(String str) => DeviceLink.fromJson(json.decode(str));

String deviceLinkToJson(DeviceLink data) => json.encode(data.toJson());

EnvironmentReading environmentReadingFromJson(String str) => EnvironmentReading.fromJson(json.decode(str));

String environmentReadingToJson(EnvironmentReading data) => json.encode(data.toJson());

PresenceReading presenceReadingFromJson(String str) => PresenceReading.fromJson(json.decode(str));

String presenceReadingToJson(PresenceReading data) => json.encode(data.toJson());

OccupancySnapshot occupancySnapshotFromJson(String str) => OccupancySnapshot.fromJson(json.decode(str));

String occupancySnapshotToJson(OccupancySnapshot data) => json.encode(data.toJson());

ZoneOccupancy zoneOccupancyFromJson(String str) => ZoneOccupancy.fromJson(json.decode(str));

String zoneOccupancyToJson(ZoneOccupancy data) => json.encode(data.toJson());

LightingState lightingStateFromJson(String str) => LightingState.fromJson(json.decode(str));

String lightingStateToJson(LightingState data) => json.encode(data.toJson());

LightingModeRequest lightingModeRequestFromJson(String str) => LightingModeRequest.fromJson(json.decode(str));

String lightingModeRequestToJson(LightingModeRequest data) => json.encode(data.toJson());

ZoneLightRequest zoneLightRequestFromJson(String str) => ZoneLightRequest.fromJson(json.decode(str));

String zoneLightRequestToJson(ZoneLightRequest data) => json.encode(data.toJson());

Lecture lectureFromJson(String str) => Lecture.fromJson(json.decode(str));

String lectureToJson(Lecture data) => json.encode(data.toJson());

LectureCreate lectureCreateFromJson(String str) => LectureCreate.fromJson(json.decode(str));

String lectureCreateToJson(LectureCreate data) => json.encode(data.toJson());

LecturePatch lecturePatchFromJson(String str) => LecturePatch.fromJson(json.decode(str));

String lecturePatchToJson(LecturePatch data) => json.encode(data.toJson());

CurrentLecture currentLectureFromJson(String str) => CurrentLecture.fromJson(json.decode(str));

String currentLectureToJson(CurrentLecture data) => json.encode(data.toJson());

LectureEvent lectureEventFromJson(String str) => LectureEvent.fromJson(json.decode(str));

String lectureEventToJson(LectureEvent data) => json.encode(data.toJson());

ClimateState climateStateFromJson(String str) => ClimateState.fromJson(json.decode(str));

String climateStateToJson(ClimateState data) => json.encode(data.toJson());

ClimateTargetRequest climateTargetRequestFromJson(String str) => ClimateTargetRequest.fromJson(json.decode(str));

String climateTargetRequestToJson(ClimateTargetRequest data) => json.encode(data.toJson());

PresentationState presentationStateFromJson(String str) => PresentationState.fromJson(json.decode(str));

String presentationStateToJson(PresentationState data) => json.encode(data.toJson());

PresentationRequest presentationRequestFromJson(String str) => PresentationRequest.fromJson(json.decode(str));

String presentationRequestToJson(PresentationRequest data) => json.encode(data.toJson());

Announcement announcementFromJson(String str) => Announcement.fromJson(json.decode(str));

String announcementToJson(Announcement data) => json.encode(data.toJson());

PlayAnnouncementRequest playAnnouncementRequestFromJson(String str) => PlayAnnouncementRequest.fromJson(json.decode(str));

String playAnnouncementRequestToJson(PlayAnnouncementRequest data) => json.encode(data.toJson());

AnnouncementPlayback announcementPlaybackFromJson(String str) => AnnouncementPlayback.fromJson(json.decode(str));

String announcementPlaybackToJson(AnnouncementPlayback data) => json.encode(data.toJson());

Decision decisionFromJson(String str) => Decision.fromJson(json.decode(str));

String decisionToJson(Decision data) => json.encode(data.toJson());

Settings settingsFromJson(String str) => Settings.fromJson(json.decode(str));

String settingsToJson(Settings data) => json.encode(data.toJson());

SettingsPatch settingsPatchFromJson(String str) => SettingsPatch.fromJson(json.decode(str));

String settingsPatchToJson(SettingsPatch data) => json.encode(data.toJson());

HistorySeries historySeriesFromJson(String str) => HistorySeries.fromJson(json.decode(str));

String historySeriesToJson(HistorySeries data) => json.encode(data.toJson());

VoiceAskRequest voiceAskRequestFromJson(String str) => VoiceAskRequest.fromJson(json.decode(str));

String voiceAskRequestToJson(VoiceAskRequest data) => json.encode(data.toJson());

VoiceAskResponse voiceAskResponseFromJson(String str) => VoiceAskResponse.fromJson(json.decode(str));

String voiceAskResponseToJson(VoiceAskResponse data) => json.encode(data.toJson());

VoiceCommand voiceCommandFromJson(String str) => VoiceCommand.fromJson(json.decode(str));

String voiceCommandToJson(VoiceCommand data) => json.encode(data.toJson());

VoiceWake voiceWakeFromJson(String str) => VoiceWake.fromJson(json.decode(str));

String voiceWakeToJson(VoiceWake data) => json.encode(data.toJson());

VoiceTranscript voiceTranscriptFromJson(String str) => VoiceTranscript.fromJson(json.decode(str));

String voiceTranscriptToJson(VoiceTranscript data) => json.encode(data.toJson());

Heartbeat heartbeatFromJson(String str) => Heartbeat.fromJson(json.decode(str));

String heartbeatToJson(Heartbeat data) => json.encode(data.toJson());

BusEnvelope busEnvelopeFromJson(String str) => BusEnvelope.fromJson(json.decode(str));

String busEnvelopeToJson(BusEnvelope data) => json.encode(data.toJson());


///The only failure shape in the contract. Every non-2xx HTTP response body is an
///ErrorResponse. No endpoint defines its own error shape.
class ErrorResponse {
    
    ///What went wrong.
    final ApiError error;
    
    ///Always false on failure.
    final bool ok;

    ErrorResponse({
        required this.error,
        required this.ok,
    });

    ErrorResponse copyWith({
        ApiError? error,
        bool? ok,
    }) => 
        ErrorResponse(
            error: error ?? this.error,
            ok: ok ?? this.ok,
        );

    factory ErrorResponse.fromJson(Map<String, dynamic> json) => ErrorResponse(
        error: ApiError.fromJson(json["error"]),
        ok: json["ok"],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "error": error.toJson(),
        "ok": ok,
    }..removeWhere((_, value) => value == null);
}


///What went wrong.
///
///Failure details.
class ApiError {
    
    ///Machine readable failure code.
    final ErrorCode code;
    
    ///Machine readable context, an empty object when there is none. Conventional keys:
    ///invalid_input uses field and reason, not_found uses resource and id, conflict uses
    ///resource and conflicting_id, device_unavailable uses device, reason and last_seen_at,
    ///device_timeout uses device, cmd_id and attempts, degraded uses components.
    final Map<String, dynamic> detail;
    
    ///One English sentence for logs and toasts. Never parsed by clients.
    final String message;

    ApiError({
        required this.code,
        required this.detail,
        required this.message,
    });

    ApiError copyWith({
        ErrorCode? code,
        Map<String, dynamic>? detail,
        String? message,
    }) => 
        ApiError(
            code: code ?? this.code,
            detail: detail ?? this.detail,
            message: message ?? this.message,
        );

    factory ApiError.fromJson(Map<String, dynamic> json) => ApiError(
        code: errorCodeValues.map[json["code"]]!,
        detail: Map.from(json["detail"]).map((k, v) => MapEntry<String, dynamic>(k, v)),
        message: json["message"],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "code": errorCodeValues.reverse[code],
        "detail": Map.from(detail).map((k, v) => MapEntry<String, dynamic>(k, v)),
        "message": message,
    }..removeWhere((_, value) => value == null);
}


///Machine readable failure code.
///
///Fixed set of failure codes. HTTP status per code: invalid_input 400, unauthorized 401,
///not_found 404, conflict 409, internal 500, device_unavailable 503, degraded 503,
///device_timeout 504. Clients branch on the code, never on the message.
enum ErrorCode {
    CONFLICT,
    DEGRADED,
    DEVICE_TIMEOUT,
    DEVICE_UNAVAILABLE,
    INTERNAL,
    INVALID_INPUT,
    NOT_FOUND,
    UNAUTHORIZED
}

final errorCodeValues = EnumValues({
    "conflict": ErrorCode.CONFLICT,
    "degraded": ErrorCode.DEGRADED,
    "device_timeout": ErrorCode.DEVICE_TIMEOUT,
    "device_unavailable": ErrorCode.DEVICE_UNAVAILABLE,
    "internal": ErrorCode.INTERNAL,
    "invalid_input": ErrorCode.INVALID_INPUT,
    "not_found": ErrorCode.NOT_FOUND,
    "unauthorized": ErrorCode.UNAUTHORIZED
});


///Liveness and readiness of the Pi services, returned by GET /health. status is the worst
///status of the critical components api, bus, engine and bridge.
class HealthStatus {
    
    ///One entry per component, at most one per name.
    final List<ComponentHealth> components;
    
    ///Contract version the API was built against.
    final String contractVersion;
    
    ///API service version.
    final String serviceVersion;
    
    ///Overall status.
    final ComponentStatus status;
    
    ///Server time, ISO 8601 UTC. Clients may use it to detect clock skew.
    final DateTime ts;
    
    ///Seconds since API start.
    final int uptimeS;

    HealthStatus({
        required this.components,
        required this.contractVersion,
        required this.serviceVersion,
        required this.status,
        required this.ts,
        required this.uptimeS,
    });

    HealthStatus copyWith({
        List<ComponentHealth>? components,
        String? contractVersion,
        String? serviceVersion,
        ComponentStatus? status,
        DateTime? ts,
        int? uptimeS,
    }) => 
        HealthStatus(
            components: components ?? this.components,
            contractVersion: contractVersion ?? this.contractVersion,
            serviceVersion: serviceVersion ?? this.serviceVersion,
            status: status ?? this.status,
            ts: ts ?? this.ts,
            uptimeS: uptimeS ?? this.uptimeS,
        );

    factory HealthStatus.fromJson(Map<String, dynamic> json) => HealthStatus(
        components: List<ComponentHealth>.from(json["components"].map((x) => ComponentHealth.fromJson(x))),
        contractVersion: json["contract_version"],
        serviceVersion: json["service_version"],
        status: componentStatusValues.map[json["status"]]!,
        ts: DateTime.parse(json["ts"]),
        uptimeS: json["uptime_s"],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "components": List<dynamic>.from(components.map((x) => x.toJson())),
        "contract_version": contractVersion,
        "service_version": serviceVersion,
        "status": componentStatusValues.reverse[status],
        "ts": ts.toIso8601String(),
        "uptime_s": uptimeS,
    }..removeWhere((_, value) => value == null);
}


///Health of one component.
class ComponentHealth {
    
    ///Short explanation when not ok, otherwise null.
    final String? detail;
    
    ///Last bus message or heartbeat from the component, ISO 8601 UTC, null when never seen.
    final DateTime? lastSeenAt;
    
    ///Component.
    final HealthComponent name;
    
    ///Component status.
    final ComponentStatus status;

    ComponentHealth({
        required this.detail,
        required this.lastSeenAt,
        required this.name,
        required this.status,
    });

    ComponentHealth copyWith({
        String? detail,
        DateTime? lastSeenAt,
        HealthComponent? name,
        ComponentStatus? status,
    }) => 
        ComponentHealth(
            detail: detail ?? this.detail,
            lastSeenAt: lastSeenAt ?? this.lastSeenAt,
            name: name ?? this.name,
            status: status ?? this.status,
        );

    factory ComponentHealth.fromJson(Map<String, dynamic> json) => ComponentHealth(
        detail: json["detail"],
        lastSeenAt: json["last_seen_at"] == null ? null : DateTime.parse(json["last_seen_at"]),
        name: healthComponentValues.map[json["name"]]!,
        status: componentStatusValues.map[json["status"]]!,
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "detail": detail,
        "last_seen_at": lastSeenAt?.toIso8601String(),
        "name": healthComponentValues.reverse[name],
        "status": componentStatusValues.reverse[status],
    }..removeWhere((_, value) => value == null);
}


///Component.
///
///Component reported in health. esp32 is the microcontroller as seen through the bridge,
///database is the API store.
enum HealthComponent {
    API,
    BRIDGE,
    BUS,
    DATABASE,
    ENGINE,
    ESP32,
    RECORDER,
    VISION,
    VOICE
}

final healthComponentValues = EnumValues({
    "api": HealthComponent.API,
    "bridge": HealthComponent.BRIDGE,
    "bus": HealthComponent.BUS,
    "database": HealthComponent.DATABASE,
    "engine": HealthComponent.ENGINE,
    "esp32": HealthComponent.ESP32,
    "recorder": HealthComponent.RECORDER,
    "vision": HealthComponent.VISION,
    "voice": HealthComponent.VOICE
});


///Component status.
///
///Health of a component or of the whole system. ok works as specified, degraded works with
///reduced function or stale data, down does not work.
///
///Overall status.
///
///Vision camera.
///
///Microcontroller link.
///
///Voice microphone.
///
///Room speaker.
///
///Overall status, degraded when any device is not ok.
///
///Link status.
///
///Self-reported status.
enum ComponentStatus {
    DEGRADED,
    DOWN,
    OK
}

final componentStatusValues = EnumValues({
    "degraded": ComponentStatus.DEGRADED,
    "down": ComponentStatus.DOWN,
    "ok": ComponentStatus.OK
});


///Everything the app needs for its home screen in one object. Published by the engine on
///system.state, retained, and returned by GET /state.
class SystemState {
    
    ///Climate state.
    final ClimateState climate;
    
    ///Contract version of the publisher.
    final String contractVersion;
    
    ///Per-device status.
    final DeviceStatuses devices;
    
    ///Latest environment reading, null before the first report.
    final EnvironmentReading? environment;
    
    ///Current and next lecture.
    final CurrentLecture lecture;
    
    ///Lighting state.
    final LightingState lighting;
    
    ///Latest fused occupancy, null before the first estimate.
    final OccupancySnapshot? occupancy;
    
    ///Presentation state.
    final PresentationState presentation;
    
    ///Overall status, degraded when any device is not ok.
    final ComponentStatus status;
    
    ///Snapshot time, ISO 8601 UTC.
    final DateTime ts;

    SystemState({
        required this.climate,
        required this.contractVersion,
        required this.devices,
        required this.environment,
        required this.lecture,
        required this.lighting,
        required this.occupancy,
        required this.presentation,
        required this.status,
        required this.ts,
    });

    SystemState copyWith({
        ClimateState? climate,
        String? contractVersion,
        DeviceStatuses? devices,
        EnvironmentReading? environment,
        CurrentLecture? lecture,
        LightingState? lighting,
        OccupancySnapshot? occupancy,
        PresentationState? presentation,
        ComponentStatus? status,
        DateTime? ts,
    }) => 
        SystemState(
            climate: climate ?? this.climate,
            contractVersion: contractVersion ?? this.contractVersion,
            devices: devices ?? this.devices,
            environment: environment ?? this.environment,
            lecture: lecture ?? this.lecture,
            lighting: lighting ?? this.lighting,
            occupancy: occupancy ?? this.occupancy,
            presentation: presentation ?? this.presentation,
            status: status ?? this.status,
            ts: ts ?? this.ts,
        );

    factory SystemState.fromJson(Map<String, dynamic> json) => SystemState(
        climate: ClimateState.fromJson(json["climate"]),
        contractVersion: json["contract_version"],
        devices: DeviceStatuses.fromJson(json["devices"]),
        environment: json["environment"] == null ? null : EnvironmentReading.fromJson(json["environment"]),
        lecture: CurrentLecture.fromJson(json["lecture"]),
        lighting: LightingState.fromJson(json["lighting"]),
        occupancy: json["occupancy"] == null ? null : OccupancySnapshot.fromJson(json["occupancy"]),
        presentation: PresentationState.fromJson(json["presentation"]),
        status: componentStatusValues.map[json["status"]]!,
        ts: DateTime.parse(json["ts"]),
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "climate": climate.toJson(),
        "contract_version": contractVersion,
        "devices": devices.toJson(),
        "environment": environment?.toJson(),
        "lecture": lecture.toJson(),
        "lighting": lighting.toJson(),
        "occupancy": occupancy?.toJson(),
        "presentation": presentation.toJson(),
        "status": componentStatusValues.reverse[status],
        "ts": ts.toIso8601String(),
    }..removeWhere((_, value) => value == null);
}


///Climate state.
///
///Climate as known by the engine. The only actuator is the ESP32 fan. In auto mode the
///engine raises fan speed while temperature_c is above target_c plus the hysteresis from
///Settings and lowers it below target_c minus the hysteresis.
class ClimateState {
    
    ///True when the device acknowledged fan_speed.
    final bool fanConfirmed;
    
    ///Commanded fan speed, percent 0 to 100.
    final int fanSpeed;
    
    ///Latest relative humidity, percent, integer 0 to 100, null when unknown.
    final int? humidityPct;
    
    ///Active climate mode.
    final ClimateMode mode;
    
    ///Target temperature, degrees Celsius, one decimal place, 16.0 to 30.0.
    final double targetC;
    
    ///Latest air temperature, degrees Celsius, one decimal place, null when unknown.
    final double? temperatureC;
    
    ///Last change of any field, ISO 8601 UTC.
    final DateTime updatedAt;

    ClimateState({
        required this.fanConfirmed,
        required this.fanSpeed,
        required this.humidityPct,
        required this.mode,
        required this.targetC,
        required this.temperatureC,
        required this.updatedAt,
    });

    ClimateState copyWith({
        bool? fanConfirmed,
        int? fanSpeed,
        int? humidityPct,
        ClimateMode? mode,
        double? targetC,
        double? temperatureC,
        DateTime? updatedAt,
    }) => 
        ClimateState(
            fanConfirmed: fanConfirmed ?? this.fanConfirmed,
            fanSpeed: fanSpeed ?? this.fanSpeed,
            humidityPct: humidityPct ?? this.humidityPct,
            mode: mode ?? this.mode,
            targetC: targetC ?? this.targetC,
            temperatureC: temperatureC ?? this.temperatureC,
            updatedAt: updatedAt ?? this.updatedAt,
        );

    factory ClimateState.fromJson(Map<String, dynamic> json) => ClimateState(
        fanConfirmed: json["fan_confirmed"],
        fanSpeed: json["fan_speed"],
        humidityPct: json["humidity_pct"],
        mode: climateModeValues.map[json["mode"]]!,
        targetC: json["target_c"]?.toDouble(),
        temperatureC: json["temperature_c"]?.toDouble(),
        updatedAt: DateTime.parse(json["updated_at"]),
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "fan_confirmed": fanConfirmed,
        "fan_speed": fanSpeed,
        "humidity_pct": humidityPct,
        "mode": climateModeValues.reverse[mode],
        "target_c": targetC,
        "temperature_c": temperatureC,
        "updated_at": updatedAt.toIso8601String(),
    }..removeWhere((_, value) => value == null);
}


///Active climate mode.
///
///Climate mode. auto drives the fan toward the target temperature, manual holds the last
///fan speed, off stops the fan.
enum ClimateMode {
    AUTO,
    MANUAL,
    OFF
}

final climateModeValues = EnumValues({
    "auto": ClimateMode.AUTO,
    "manual": ClimateMode.MANUAL,
    "off": ClimateMode.OFF
});


///Per-device status.
///
///Status of the physical devices.
class DeviceStatuses {
    
    ///Vision camera.
    final ComponentStatus camera;
    
    ///Microcontroller link.
    final ComponentStatus esp32;
    
    ///Voice microphone.
    final ComponentStatus microphone;
    
    ///Room speaker.
    final ComponentStatus speaker;

    DeviceStatuses({
        required this.camera,
        required this.esp32,
        required this.microphone,
        required this.speaker,
    });

    DeviceStatuses copyWith({
        ComponentStatus? camera,
        ComponentStatus? esp32,
        ComponentStatus? microphone,
        ComponentStatus? speaker,
    }) => 
        DeviceStatuses(
            camera: camera ?? this.camera,
            esp32: esp32 ?? this.esp32,
            microphone: microphone ?? this.microphone,
            speaker: speaker ?? this.speaker,
        );

    factory DeviceStatuses.fromJson(Map<String, dynamic> json) => DeviceStatuses(
        camera: componentStatusValues.map[json["camera"]]!,
        esp32: componentStatusValues.map[json["esp32"]]!,
        microphone: componentStatusValues.map[json["microphone"]]!,
        speaker: componentStatusValues.map[json["speaker"]]!,
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "camera": componentStatusValues.reverse[camera],
        "esp32": componentStatusValues.reverse[esp32],
        "microphone": componentStatusValues.reverse[microphone],
        "speaker": componentStatusValues.reverse[speaker],
    }..removeWhere((_, value) => value == null);
}


///One environment sample from the ESP32, normalised by the bridge from a sensor_report and
///published on sensor.environment. A value is null when that sensor failed in this cycle.
class EnvironmentReading {
    
    ///Reporting device.
    final String deviceId;
    
    ///Relative humidity, percent, integer 0 to 100, null on sensor fault.
    final int? humidityPct;
    
    ///Ambient illuminance at the light sensor, lux, integer 0 to 100000, null on sensor fault.
    final int? lux;
    
    ///Air temperature, degrees Celsius, one decimal place, -40.0 to 85.0, null on sensor fault.
    final double? temperatureC;
    
    ///Sample time, ISO 8601 UTC, as stamped by the Pi on receipt.
    final DateTime ts;

    EnvironmentReading({
        required this.deviceId,
        required this.humidityPct,
        required this.lux,
        required this.temperatureC,
        required this.ts,
    });

    EnvironmentReading copyWith({
        String? deviceId,
        int? humidityPct,
        int? lux,
        double? temperatureC,
        DateTime? ts,
    }) => 
        EnvironmentReading(
            deviceId: deviceId ?? this.deviceId,
            humidityPct: humidityPct ?? this.humidityPct,
            lux: lux ?? this.lux,
            temperatureC: temperatureC ?? this.temperatureC,
            ts: ts ?? this.ts,
        );

    factory EnvironmentReading.fromJson(Map<String, dynamic> json) => EnvironmentReading(
        deviceId: json["device_id"],
        humidityPct: json["humidity_pct"],
        lux: json["lux"],
        temperatureC: json["temperature_c"]?.toDouble(),
        ts: DateTime.parse(json["ts"]),
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "device_id": deviceId,
        "humidity_pct": humidityPct,
        "lux": lux,
        "temperature_c": temperatureC,
        "ts": ts.toIso8601String(),
    }..removeWhere((_, value) => value == null);
}


///Current and next lecture.
///
///The lecture in progress at a given instant and the next one after it. Returned by GET
////lectures/current.
class CurrentLecture {
    
    ///Instant the answer refers to, ISO 8601 UTC.
    final DateTime at;
    
    ///Lecture with status in_progress at that instant, null when none.
    final Lecture? current;
    
    ///First scheduled lecture starting after that instant, null when none in the next 7 days.
    final Lecture? next;

    CurrentLecture({
        required this.at,
        required this.current,
        required this.next,
    });

    CurrentLecture copyWith({
        DateTime? at,
        Lecture? current,
        Lecture? next,
    }) => 
        CurrentLecture(
            at: at ?? this.at,
            current: current ?? this.current,
            next: next ?? this.next,
        );

    factory CurrentLecture.fromJson(Map<String, dynamic> json) => CurrentLecture(
        at: DateTime.parse(json["at"]),
        current: json["current"] == null ? null : Lecture.fromJson(json["current"]),
        next: json["next"] == null ? null : Lecture.fromJson(json["next"]),
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "at": at.toIso8601String(),
        "current": current?.toJson(),
        "next": next?.toJson(),
    }..removeWhere((_, value) => value == null);
}


///A scheduled lecture in the room. Owned and stored by the API. status is derived by the
///API from the clock and is read-only. ends_at is strictly after starts_at and two lectures
///that are not cancelled never overlap.
///
///Lecture after the change, or the last state for deleted.
class Lecture {
    
    ///Course code such as CS301, null when not set.
    final String? courseCode;
    
    ///Creation time, ISO 8601 UTC.
    final DateTime createdAt;
    
    ///End, ISO 8601 UTC, strictly after starts_at.
    final DateTime endsAt;
    
    ///Lecture identifier.
    final String id;
    
    ///Lecturer display name, 1 to 80 characters.
    final String lecturer;
    
    ///Lighting mode the engine applies when the lecture starts.
    final LectureLightingMode lightingMode;
    
    ///Free text, at most 500 characters, null when empty.
    final String? notes;
    
    ///Start, ISO 8601 UTC.
    final DateTime startsAt;
    
    ///Lifecycle status, derived by the API.
    final LectureStatus status;
    
    ///Lecture title, 1 to 120 characters.
    final String title;
    
    ///Last modification time, ISO 8601 UTC.
    final DateTime updatedAt;

    Lecture({
        required this.courseCode,
        required this.createdAt,
        required this.endsAt,
        required this.id,
        required this.lecturer,
        required this.lightingMode,
        required this.notes,
        required this.startsAt,
        required this.status,
        required this.title,
        required this.updatedAt,
    });

    Lecture copyWith({
        String? courseCode,
        DateTime? createdAt,
        DateTime? endsAt,
        String? id,
        String? lecturer,
        LectureLightingMode? lightingMode,
        String? notes,
        DateTime? startsAt,
        LectureStatus? status,
        String? title,
        DateTime? updatedAt,
    }) => 
        Lecture(
            courseCode: courseCode ?? this.courseCode,
            createdAt: createdAt ?? this.createdAt,
            endsAt: endsAt ?? this.endsAt,
            id: id ?? this.id,
            lecturer: lecturer ?? this.lecturer,
            lightingMode: lightingMode ?? this.lightingMode,
            notes: notes ?? this.notes,
            startsAt: startsAt ?? this.startsAt,
            status: status ?? this.status,
            title: title ?? this.title,
            updatedAt: updatedAt ?? this.updatedAt,
        );

    factory Lecture.fromJson(Map<String, dynamic> json) => Lecture(
        courseCode: json["course_code"],
        createdAt: DateTime.parse(json["created_at"]),
        endsAt: DateTime.parse(json["ends_at"]),
        id: json["id"],
        lecturer: json["lecturer"],
        lightingMode: lectureLightingModeValues.map[json["lighting_mode"]]!,
        notes: json["notes"],
        startsAt: DateTime.parse(json["starts_at"]),
        status: lectureStatusValues.map[json["status"]]!,
        title: json["title"],
        updatedAt: DateTime.parse(json["updated_at"]),
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "course_code": courseCode,
        "created_at": createdAt.toIso8601String(),
        "ends_at": endsAt.toIso8601String(),
        "id": id,
        "lecturer": lecturer,
        "lighting_mode": lectureLightingModeValues.reverse[lightingMode],
        "notes": notes,
        "starts_at": startsAt.toIso8601String(),
        "status": lectureStatusValues.reverse[status],
        "title": title,
        "updated_at": updatedAt.toIso8601String(),
    }..removeWhere((_, value) => value == null);
}


///Lighting mode the engine applies when the lecture starts.
///
///Lighting mode a lecture starts in.
///
///Lighting mode applied at start, default lecture.
///
///Lighting mode applied at start.
enum LectureLightingMode {
    LECTURE,
    PRESENTATION
}

final lectureLightingModeValues = EnumValues({
    "lecture": LectureLightingMode.LECTURE,
    "presentation": LectureLightingMode.PRESENTATION
});


///Lifecycle status, derived by the API.
///
///scheduled before starts_at, in_progress between starts_at and ends_at, finished after
///ends_at, cancelled when cancelled by a person. Finished and cancelled lectures are
///read-only.
enum LectureStatus {
    CANCELLED,
    FINISHED,
    IN_PROGRESS,
    SCHEDULED
}

final lectureStatusValues = EnumValues({
    "cancelled": LectureStatus.CANCELLED,
    "finished": LectureStatus.FINISHED,
    "in_progress": LectureStatus.IN_PROGRESS,
    "scheduled": LectureStatus.SCHEDULED
});


///Lighting state.
///
///Lighting as known by the engine. brightness is the last value the engine commanded.
///confirmed is true once the ESP32 acknowledged that value. zones holds exactly one entry
///per zone in canonical order.
class LightingState {
    
    ///Latest ambient illuminance used by auto mode, lux, integer 0 to 100000, null when unknown.
    final int? ambientLux;
    
    ///Active lighting mode.
    final LightingMode mode;
    
    ///Last change of any field, ISO 8601 UTC.
    final DateTime updatedAt;
    
    ///Per-zone lighting, one entry per zone, order left, center, right, professor.
    final List<ZoneLight> zones;

    LightingState({
        required this.ambientLux,
        required this.mode,
        required this.updatedAt,
        required this.zones,
    });

    LightingState copyWith({
        int? ambientLux,
        LightingMode? mode,
        DateTime? updatedAt,
        List<ZoneLight>? zones,
    }) => 
        LightingState(
            ambientLux: ambientLux ?? this.ambientLux,
            mode: mode ?? this.mode,
            updatedAt: updatedAt ?? this.updatedAt,
            zones: zones ?? this.zones,
        );

    factory LightingState.fromJson(Map<String, dynamic> json) => LightingState(
        ambientLux: json["ambient_lux"],
        mode: lightingModeValues.map[json["mode"]]!,
        updatedAt: DateTime.parse(json["updated_at"]),
        zones: List<ZoneLight>.from(json["zones"].map((x) => ZoneLight.fromJson(x))),
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "ambient_lux": ambientLux,
        "mode": lightingModeValues.reverse[mode],
        "updated_at": updatedAt.toIso8601String(),
        "zones": List<dynamic>.from(zones.map((x) => x.toJson())),
    }..removeWhere((_, value) => value == null);
}


///Active lighting mode.
///
///Lighting mode. auto follows occupancy and daylight, lecture and presentation apply the
///presets from Settings, manual holds per-zone values set by a person, off forces every
///zone to 0.
///
///Lighting mode for lighting.set_mode.
enum LightingMode {
    AUTO,
    LECTURE,
    MANUAL,
    OFF,
    PRESENTATION
}

final lightingModeValues = EnumValues({
    "auto": LightingMode.AUTO,
    "lecture": LightingMode.LECTURE,
    "manual": LightingMode.MANUAL,
    "off": LightingMode.OFF,
    "presentation": LightingMode.PRESENTATION
});


///Light of one zone.
class ZoneLight {
    
    ///Commanded brightness, percent 0 to 100.
    final int brightness;
    
    ///True when the device acknowledged this brightness, false while pending or after an ack
    ///timeout.
    final bool confirmed;
    
    ///Time brightness was last commanded, ISO 8601 UTC.
    final DateTime updatedAt;
    
    ///Zone.
    final Zone zone;

    ZoneLight({
        required this.brightness,
        required this.confirmed,
        required this.updatedAt,
        required this.zone,
    });

    ZoneLight copyWith({
        int? brightness,
        bool? confirmed,
        DateTime? updatedAt,
        Zone? zone,
    }) => 
        ZoneLight(
            brightness: brightness ?? this.brightness,
            confirmed: confirmed ?? this.confirmed,
            updatedAt: updatedAt ?? this.updatedAt,
            zone: zone ?? this.zone,
        );

    factory ZoneLight.fromJson(Map<String, dynamic> json) => ZoneLight(
        brightness: json["brightness"],
        confirmed: json["confirmed"],
        updatedAt: DateTime.parse(json["updated_at"]),
        zone: zoneValues.map[json["zone"]]!,
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "brightness": brightness,
        "confirmed": confirmed,
        "updated_at": updatedAt.toIso8601String(),
        "zone": zoneValues.reverse[zone],
    }..removeWhere((_, value) => value == null);
}


///Zone.
///
///Room zone. Canonical order is left, center, right, professor. Left and right are as seen
///by the audience facing the board.
enum Zone {
    CENTER,
    LEFT,
    PROFESSOR,
    RIGHT
}

final zoneValues = EnumValues({
    "center": Zone.CENTER,
    "left": Zone.LEFT,
    "professor": Zone.PROFESSOR,
    "right": Zone.RIGHT
});


///Room occupancy at one instant. Published by vision on perception.occupancy with source
///vision. The engine serves its fused view on GET /occupancy, which falls back to PIR
///presence with source presence and null counts when vision is stale. zones holds exactly
///one entry per zone in canonical order. total is the sum of zone counts, null when any
///count is null.
class OccupancySnapshot {
    
    ///Overall confidence, 0.0 to 1.0.
    final double confidence;
    
    ///Camera frame counter from vision, null when source is presence.
    final int? frameId;
    
    ///True when any zone is occupied.
    final bool occupied;
    
    ///Where the estimate comes from.
    final OccupancySource source;
    
    ///True when the newest input is older than 5 seconds. Consumers must not switch lights off
    ///on stale data.
    final bool stale;
    
    ///People in the room, integer 0 to 500, null when counts are unknown.
    final int? total;
    
    ///Capture time of the frame or reading, ISO 8601 UTC.
    final DateTime ts;
    
    ///Per-zone occupancy, one entry per zone, order left, center, right, professor.
    final List<ZoneOccupancy> zones;

    OccupancySnapshot({
        required this.confidence,
        required this.frameId,
        required this.occupied,
        required this.source,
        required this.stale,
        required this.total,
        required this.ts,
        required this.zones,
    });

    OccupancySnapshot copyWith({
        double? confidence,
        int? frameId,
        bool? occupied,
        OccupancySource? source,
        bool? stale,
        int? total,
        DateTime? ts,
        List<ZoneOccupancy>? zones,
    }) => 
        OccupancySnapshot(
            confidence: confidence ?? this.confidence,
            frameId: frameId ?? this.frameId,
            occupied: occupied ?? this.occupied,
            source: source ?? this.source,
            stale: stale ?? this.stale,
            total: total ?? this.total,
            ts: ts ?? this.ts,
            zones: zones ?? this.zones,
        );

    factory OccupancySnapshot.fromJson(Map<String, dynamic> json) => OccupancySnapshot(
        confidence: json["confidence"]?.toDouble(),
        frameId: json["frame_id"],
        occupied: json["occupied"],
        source: occupancySourceValues.map[json["source"]]!,
        stale: json["stale"],
        total: json["total"],
        ts: DateTime.parse(json["ts"]),
        zones: List<ZoneOccupancy>.from(json["zones"].map((x) => ZoneOccupancy.fromJson(x))),
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "confidence": confidence,
        "frame_id": frameId,
        "occupied": occupied,
        "source": occupancySourceValues.reverse[source],
        "stale": stale,
        "total": total,
        "ts": ts.toIso8601String(),
        "zones": List<dynamic>.from(zones.map((x) => x.toJson())),
    }..removeWhere((_, value) => value == null);
}


///Where the estimate comes from.
///
///vision is the camera people counter, presence is the PIR fallback.
enum OccupancySource {
    PRESENCE,
    VISION
}

final occupancySourceValues = EnumValues({
    "presence": OccupancySource.PRESENCE,
    "vision": OccupancySource.VISION
});


///Occupancy of one zone.
class ZoneOccupancy {
    
    ///Confidence of this zone estimate, 0.0 to 1.0.
    final double confidence;
    
    ///People in the zone, integer 0 to 500, null when only presence is known, for example when
    ///the camera is down.
    final int? count;
    
    ///True when at least one person is in the zone.
    final bool occupied;
    
    ///Zone.
    final Zone zone;

    ZoneOccupancy({
        required this.confidence,
        required this.count,
        required this.occupied,
        required this.zone,
    });

    ZoneOccupancy copyWith({
        double? confidence,
        int? count,
        bool? occupied,
        Zone? zone,
    }) => 
        ZoneOccupancy(
            confidence: confidence ?? this.confidence,
            count: count ?? this.count,
            occupied: occupied ?? this.occupied,
            zone: zone ?? this.zone,
        );

    factory ZoneOccupancy.fromJson(Map<String, dynamic> json) => ZoneOccupancy(
        confidence: json["confidence"]?.toDouble(),
        count: json["count"],
        occupied: json["occupied"],
        zone: zoneValues.map[json["zone"]]!,
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "confidence": confidence,
        "count": count,
        "occupied": occupied,
        "zone": zoneValues.reverse[zone],
    }..removeWhere((_, value) => value == null);
}


///Presentation state.
///
///Presentation mode. While active the lighting mode is presentation unless a person
///overrides a zone.
class PresentationState {
    
    ///True while presentation mode is on.
    final bool active;
    
    ///Lecture the presentation belongs to, null when none.
    final String? lectureId;
    
    ///Start of the current presentation, ISO 8601 UTC, null when inactive.
    final DateTime? startedAt;
    
    ///Who started it, null when inactive.
    final PresentationStarter? startedBy;

    PresentationState({
        required this.active,
        required this.lectureId,
        required this.startedAt,
        required this.startedBy,
    });

    PresentationState copyWith({
        bool? active,
        String? lectureId,
        DateTime? startedAt,
        PresentationStarter? startedBy,
    }) => 
        PresentationState(
            active: active ?? this.active,
            lectureId: lectureId ?? this.lectureId,
            startedAt: startedAt ?? this.startedAt,
            startedBy: startedBy ?? this.startedBy,
        );

    factory PresentationState.fromJson(Map<String, dynamic> json) => PresentationState(
        active: json["active"],
        lectureId: json["lecture_id"],
        startedAt: json["started_at"] == null ? null : DateTime.parse(json["started_at"]),
        startedBy: presentationStarterValues.map[json["started_by"]],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "active": active,
        "lecture_id": lectureId,
        "started_at": startedAt?.toIso8601String(),
        "started_by": presentationStarterValues.reverse[startedBy],
    }..removeWhere((_, value) => value == null);
}


///app is POST /presentation, voice is a start_presentation intent, schedule is a lecture
///with lighting_mode presentation.
enum PresentationStarter {
    APP,
    SCHEDULE,
    VOICE
}

final presentationStarterValues = EnumValues({
    "app": PresentationStarter.APP,
    "schedule": PresentationStarter.SCHEDULE,
    "voice": PresentationStarter.VOICE
});


///Latest raw sensor data and ESP32 link status, returned by GET /sensors.
class SensorsSnapshot {
    
    ///ESP32 link status.
    final DeviceLink device;
    
    ///Latest environment reading, null before the first report.
    final EnvironmentReading? environment;
    
    ///Latest PIR state, null before the first report.
    final PresenceReading? presence;

    SensorsSnapshot({
        required this.device,
        required this.environment,
        required this.presence,
    });

    SensorsSnapshot copyWith({
        DeviceLink? device,
        EnvironmentReading? environment,
        PresenceReading? presence,
    }) => 
        SensorsSnapshot(
            device: device ?? this.device,
            environment: environment ?? this.environment,
            presence: presence ?? this.presence,
        );

    factory SensorsSnapshot.fromJson(Map<String, dynamic> json) => SensorsSnapshot(
        device: DeviceLink.fromJson(json["device"]),
        environment: json["environment"] == null ? null : EnvironmentReading.fromJson(json["environment"]),
        presence: json["presence"] == null ? null : PresenceReading.fromJson(json["presence"]),
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "device": device.toJson(),
        "environment": environment?.toJson(),
        "presence": presence?.toJson(),
    }..removeWhere((_, value) => value == null);
}


///ESP32 link status.
///
///State of the serial link to the ESP32 as seen by the bridge. Published on device.status,
///retained, on every change and every 5 seconds, and returned inside GET /sensors. ok means
///frames and acks flow, degraded means acks are timing out, down means no frame for 15
///seconds, the port is closed or the protocol major differs.
class DeviceLink {
    
    ///Device identifier from the last hello, null before the first hello.
    final String? deviceId;
    
    ///Firmware version from the last hello, null before the first hello.
    final String? fwVersion;
    
    ///Most recent error message from the device, null when none since the last hello.
    final LinkError? lastError;
    
    ///Receipt time of the last uplink frame on the Pi clock, ISO 8601 UTC, null when none since
    ///bridge start.
    final DateTime? lastSeenAt;
    
    ///Protocol version from the last hello, null before the first hello.
    final String? protocolVersion;
    
    ///Why the link is not ok, null when ok.
    final LinkReason? reason;
    
    ///Link status.
    final ComponentStatus status;
    
    ///Device uptime from the latest hello or sensor_report, milliseconds, null before the first
    ///frame. A decrease means the device rebooted.
    final int? uptimeMs;

    DeviceLink({
        required this.deviceId,
        required this.fwVersion,
        required this.lastError,
        required this.lastSeenAt,
        required this.protocolVersion,
        required this.reason,
        required this.status,
        required this.uptimeMs,
    });

    DeviceLink copyWith({
        String? deviceId,
        String? fwVersion,
        LinkError? lastError,
        DateTime? lastSeenAt,
        String? protocolVersion,
        LinkReason? reason,
        ComponentStatus? status,
        int? uptimeMs,
    }) => 
        DeviceLink(
            deviceId: deviceId ?? this.deviceId,
            fwVersion: fwVersion ?? this.fwVersion,
            lastError: lastError ?? this.lastError,
            lastSeenAt: lastSeenAt ?? this.lastSeenAt,
            protocolVersion: protocolVersion ?? this.protocolVersion,
            reason: reason ?? this.reason,
            status: status ?? this.status,
            uptimeMs: uptimeMs ?? this.uptimeMs,
        );

    factory DeviceLink.fromJson(Map<String, dynamic> json) => DeviceLink(
        deviceId: json["device_id"],
        fwVersion: json["fw_version"],
        lastError: json["last_error"] == null ? null : LinkError.fromJson(json["last_error"]),
        lastSeenAt: json["last_seen_at"] == null ? null : DateTime.parse(json["last_seen_at"]),
        protocolVersion: json["protocol_version"],
        reason: linkReasonValues.map[json["reason"]],
        status: componentStatusValues.map[json["status"]]!,
        uptimeMs: json["uptime_ms"],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "device_id": deviceId,
        "fw_version": fwVersion,
        "last_error": lastError?.toJson(),
        "last_seen_at": lastSeenAt?.toIso8601String(),
        "protocol_version": protocolVersion,
        "reason": linkReasonValues.reverse[reason],
        "status": componentStatusValues.reverse[status],
        "uptime_ms": uptimeMs,
    }..removeWhere((_, value) => value == null);
}


///Last error frame received from the device.
class LinkError {
    
    ///Error code from the device.
    final DeviceErrorCode code;
    
    ///Error message from the device.
    final String message;
    
    ///Receipt time on the Pi clock, ISO 8601 UTC.
    final DateTime receivedAt;

    LinkError({
        required this.code,
        required this.message,
        required this.receivedAt,
    });

    LinkError copyWith({
        DeviceErrorCode? code,
        String? message,
        DateTime? receivedAt,
    }) => 
        LinkError(
            code: code ?? this.code,
            message: message ?? this.message,
            receivedAt: receivedAt ?? this.receivedAt,
        );

    factory LinkError.fromJson(Map<String, dynamic> json) => LinkError(
        code: deviceErrorCodeValues.map[json["code"]]!,
        message: json["message"],
        receivedAt: DateTime.parse(json["received_at"]),
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "code": deviceErrorCodeValues.reverse[code],
        "message": message,
        "received_at": receivedAt.toIso8601String(),
    }..removeWhere((_, value) => value == null);
}


///Error code from the device.
///
///parse_error means the line is not valid JSON, line_too_long means it exceeded 512 bytes,
///sensor_fault and actuator_fault mean hardware stopped responding, queue_full means a line
///was dropped, internal is anything else.
enum DeviceErrorCode {
    ACTUATOR_FAULT,
    INTERNAL,
    LINE_TOO_LONG,
    PARSE_ERROR,
    QUEUE_FULL,
    SENSOR_FAULT
}

final deviceErrorCodeValues = EnumValues({
    "actuator_fault": DeviceErrorCode.ACTUATOR_FAULT,
    "internal": DeviceErrorCode.INTERNAL,
    "line_too_long": DeviceErrorCode.LINE_TOO_LONG,
    "parse_error": DeviceErrorCode.PARSE_ERROR,
    "queue_full": DeviceErrorCode.QUEUE_FULL,
    "sensor_fault": DeviceErrorCode.SENSOR_FAULT
});


///no_hello means frames arrive but no hello yet, ack_timeouts means 2 or more consecutive
///commands timed out, silent means no uplink frame for 15 seconds, port_closed means the
///serial port is missing or failed, protocol_mismatch means the device protocol major
///differs from the contract major.
enum LinkReason {
    ACK_TIMEOUTS,
    NO_HELLO,
    PORT_CLOSED,
    PROTOCOL_MISMATCH,
    SILENT
}

final linkReasonValues = EnumValues({
    "ack_timeouts": LinkReason.ACK_TIMEOUTS,
    "no_hello": LinkReason.NO_HELLO,
    "port_closed": LinkReason.PORT_CLOSED,
    "protocol_mismatch": LinkReason.PROTOCOL_MISMATCH,
    "silent": LinkReason.SILENT
});


///PIR motion state per zone, normalised by the bridge from a sensor_report and published on
///sensor.presence. Contains one entry for each zone that has a PIR sensor, as declared in
///the device hello.
class PresenceReading {
    
    ///Reporting device.
    final String deviceId;
    
    ///Sample time, ISO 8601 UTC, as stamped by the Pi on receipt.
    final DateTime ts;
    
    ///Presence per zone with a PIR sensor, at most one entry per zone, canonical order.
    final List<ZonePresence> zones;

    PresenceReading({
        required this.deviceId,
        required this.ts,
        required this.zones,
    });

    PresenceReading copyWith({
        String? deviceId,
        DateTime? ts,
        List<ZonePresence>? zones,
    }) => 
        PresenceReading(
            deviceId: deviceId ?? this.deviceId,
            ts: ts ?? this.ts,
            zones: zones ?? this.zones,
        );

    factory PresenceReading.fromJson(Map<String, dynamic> json) => PresenceReading(
        deviceId: json["device_id"],
        ts: DateTime.parse(json["ts"]),
        zones: List<ZonePresence>.from(json["zones"].map((x) => ZonePresence.fromJson(x))),
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "device_id": deviceId,
        "ts": ts.toIso8601String(),
        "zones": List<dynamic>.from(zones.map((x) => x.toJson())),
    }..removeWhere((_, value) => value == null);
}


///PIR state of one zone.
class ZonePresence {
    
    ///True while the PIR reports motion, held for the sensor hold time of about 3 seconds.
    final bool present;
    
    ///Zone.
    final Zone zone;

    ZonePresence({
        required this.present,
        required this.zone,
    });

    ZonePresence copyWith({
        bool? present,
        Zone? zone,
    }) => 
        ZonePresence(
            present: present ?? this.present,
            zone: zone ?? this.zone,
        );

    factory ZonePresence.fromJson(Map<String, dynamic> json) => ZonePresence(
        present: json["present"],
        zone: zoneValues.map[json["zone"]]!,
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "present": present,
        "zone": zoneValues.reverse[zone],
    }..removeWhere((_, value) => value == null);
}


///Request body of POST /lighting/mode. manual is not accepted here, it is entered by POST
////lighting/zone.
class LightingModeRequest {
    
    ///Fade for every zone, milliseconds 0 to 5000, default 800.
    final int? fadeMs;
    
    ///Mode to switch to.
    final SelectableLightingMode mode;

    LightingModeRequest({
        this.fadeMs,
        required this.mode,
    });

    LightingModeRequest copyWith({
        int? fadeMs,
        SelectableLightingMode? mode,
    }) => 
        LightingModeRequest(
            fadeMs: fadeMs ?? this.fadeMs,
            mode: mode ?? this.mode,
        );

    factory LightingModeRequest.fromJson(Map<String, dynamic> json) => LightingModeRequest(
        fadeMs: json["fade_ms"],
        mode: selectableLightingModeValues.map[json["mode"]]!,
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "fade_ms": fadeMs,
        "mode": selectableLightingModeValues.reverse[mode],
    }..removeWhere((_, value) => value == null);
}


///Mode to switch to.
///
///LightingMode values a client may select directly, every mode except manual.
enum SelectableLightingMode {
    AUTO,
    LECTURE,
    OFF,
    PRESENTATION
}

final selectableLightingModeValues = EnumValues({
    "auto": SelectableLightingMode.AUTO,
    "lecture": SelectableLightingMode.LECTURE,
    "off": SelectableLightingMode.OFF,
    "presentation": SelectableLightingMode.PRESENTATION
});


///Request body of POST /lighting/zone. Sets one zone and switches the lighting mode to
///manual. The other zones keep their current brightness.
class ZoneLightRequest {
    
    ///Brightness, percent 0 to 100.
    final int brightness;
    
    ///Fade, milliseconds 0 to 5000, default 400.
    final int? fadeMs;
    
    ///Zone.
    final Zone zone;

    ZoneLightRequest({
        required this.brightness,
        this.fadeMs,
        required this.zone,
    });

    ZoneLightRequest copyWith({
        int? brightness,
        int? fadeMs,
        Zone? zone,
    }) => 
        ZoneLightRequest(
            brightness: brightness ?? this.brightness,
            fadeMs: fadeMs ?? this.fadeMs,
            zone: zone ?? this.zone,
        );

    factory ZoneLightRequest.fromJson(Map<String, dynamic> json) => ZoneLightRequest(
        brightness: json["brightness"],
        fadeMs: json["fade_ms"],
        zone: zoneValues.map[json["zone"]]!,
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "brightness": brightness,
        "fade_ms": fadeMs,
        "zone": zoneValues.reverse[zone],
    }..removeWhere((_, value) => value == null);
}


///Request body of POST /lectures. The API assigns id, status, created_at and updated_at.
///Omitted optional fields take the defaults stated below.
class LectureCreate {
    
    ///Course code, default null.
    final String? courseCode;
    
    ///End, ISO 8601 UTC, strictly after starts_at, at most 6 hours after it.
    final DateTime endsAt;
    
    ///Lecturer display name, 1 to 80 characters.
    final String lecturer;
    
    ///Lighting mode applied at start, default lecture.
    final LectureLightingMode? lightingMode;
    
    ///Free text, at most 500 characters, default null.
    final String? notes;
    
    ///Start, ISO 8601 UTC.
    final DateTime startsAt;
    
    ///Lecture title, 1 to 120 characters.
    final String title;

    LectureCreate({
        this.courseCode,
        required this.endsAt,
        required this.lecturer,
        this.lightingMode,
        this.notes,
        required this.startsAt,
        required this.title,
    });

    LectureCreate copyWith({
        String? courseCode,
        DateTime? endsAt,
        String? lecturer,
        LectureLightingMode? lightingMode,
        String? notes,
        DateTime? startsAt,
        String? title,
    }) => 
        LectureCreate(
            courseCode: courseCode ?? this.courseCode,
            endsAt: endsAt ?? this.endsAt,
            lecturer: lecturer ?? this.lecturer,
            lightingMode: lightingMode ?? this.lightingMode,
            notes: notes ?? this.notes,
            startsAt: startsAt ?? this.startsAt,
            title: title ?? this.title,
        );

    factory LectureCreate.fromJson(Map<String, dynamic> json) => LectureCreate(
        courseCode: json["course_code"],
        endsAt: DateTime.parse(json["ends_at"]),
        lecturer: json["lecturer"],
        lightingMode: lectureLightingModeValues.map[json["lighting_mode"]],
        notes: json["notes"],
        startsAt: DateTime.parse(json["starts_at"]),
        title: json["title"],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "course_code": courseCode,
        "ends_at": endsAt.toIso8601String(),
        "lecturer": lecturer,
        "lighting_mode": lectureLightingModeValues.reverse[lightingMode],
        "notes": notes,
        "starts_at": startsAt.toIso8601String(),
        "title": title,
    }..removeWhere((_, value) => value == null);
}


///Request body of PATCH /lectures/{id}. Merge semantics, only the keys present are changed,
///null is never sent. An empty string clears course_code or notes. Setting status to
///cancelled cancels the lecture, no other status value is accepted. Finished and cancelled
///lectures cannot be patched.
class LecturePatch {
    
    ///Course code, an empty string clears it.
    final String? courseCode;
    
    ///End, ISO 8601 UTC, strictly after the resulting starts_at.
    final DateTime? endsAt;
    
    ///Lecturer display name, 1 to 80 characters.
    final String? lecturer;
    
    ///Lighting mode applied at start.
    final LectureLightingMode? lightingMode;
    
    ///Free text, an empty string clears it.
    final String? notes;
    
    ///Start, ISO 8601 UTC.
    final DateTime? startsAt;
    
    ///Only cancelled is accepted.
    final LecturePatchStatus? status;
    
    ///Lecture title, 1 to 120 characters.
    final String? title;

    LecturePatch({
        this.courseCode,
        this.endsAt,
        this.lecturer,
        this.lightingMode,
        this.notes,
        this.startsAt,
        this.status,
        this.title,
    });

    LecturePatch copyWith({
        String? courseCode,
        DateTime? endsAt,
        String? lecturer,
        LectureLightingMode? lightingMode,
        String? notes,
        DateTime? startsAt,
        LecturePatchStatus? status,
        String? title,
    }) => 
        LecturePatch(
            courseCode: courseCode ?? this.courseCode,
            endsAt: endsAt ?? this.endsAt,
            lecturer: lecturer ?? this.lecturer,
            lightingMode: lightingMode ?? this.lightingMode,
            notes: notes ?? this.notes,
            startsAt: startsAt ?? this.startsAt,
            status: status ?? this.status,
            title: title ?? this.title,
        );

    factory LecturePatch.fromJson(Map<String, dynamic> json) => LecturePatch(
        courseCode: json["course_code"],
        endsAt: json["ends_at"] == null ? null : DateTime.parse(json["ends_at"]),
        lecturer: json["lecturer"],
        lightingMode: lectureLightingModeValues.map[json["lighting_mode"]],
        notes: json["notes"],
        startsAt: json["starts_at"] == null ? null : DateTime.parse(json["starts_at"]),
        status: lecturePatchStatusValues.map[json["status"]],
        title: json["title"],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "course_code": courseCode,
        "ends_at": endsAt?.toIso8601String(),
        "lecturer": lecturer,
        "lighting_mode": lectureLightingModeValues.reverse[lightingMode],
        "notes": notes,
        "starts_at": startsAt?.toIso8601String(),
        "status": lecturePatchStatusValues.reverse[status],
        "title": title,
    }..removeWhere((_, value) => value == null);
}


///Only cancelled is accepted.
///
///Status a client may set on a lecture.
enum LecturePatchStatus {
    CANCELLED
}

final lecturePatchStatusValues = EnumValues({
    "cancelled": LecturePatchStatus.CANCELLED
});


///Change in the lecture schedule, published by the API on schedule.lecture. created,
///updated and deleted follow the HTTP calls. started and ended are emitted by the API
///scheduler within 1 second of starts_at and ends_at. The engine applies lighting_mode and
///the default climate target on started.
class LectureEvent {
    
    ///What happened.
    final LectureEventType event;
    
    ///Lecture after the change, or the last state for deleted.
    final Lecture lecture;
    
    ///Event time, ISO 8601 UTC.
    final DateTime ts;

    LectureEvent({
        required this.event,
        required this.lecture,
        required this.ts,
    });

    LectureEvent copyWith({
        LectureEventType? event,
        Lecture? lecture,
        DateTime? ts,
    }) => 
        LectureEvent(
            event: event ?? this.event,
            lecture: lecture ?? this.lecture,
            ts: ts ?? this.ts,
        );

    factory LectureEvent.fromJson(Map<String, dynamic> json) => LectureEvent(
        event: lectureEventTypeValues.map[json["event"]]!,
        lecture: Lecture.fromJson(json["lecture"]),
        ts: DateTime.parse(json["ts"]),
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "event": lectureEventTypeValues.reverse[event],
        "lecture": lecture.toJson(),
        "ts": ts.toIso8601String(),
    }..removeWhere((_, value) => value == null);
}


///What happened.
///
///Schedule event type.
enum LectureEventType {
    CREATED,
    DELETED,
    ENDED,
    STARTED,
    UPDATED
}

final lectureEventTypeValues = EnumValues({
    "created": LectureEventType.CREATED,
    "deleted": LectureEventType.DELETED,
    "ended": LectureEventType.ENDED,
    "started": LectureEventType.STARTED,
    "updated": LectureEventType.UPDATED
});


///Request body of POST /climate/target. Sets the target and switches the climate mode to
///auto.
class ClimateTargetRequest {
    
    ///Target temperature, degrees Celsius, one decimal place, 16.0 to 30.0.
    final double targetC;

    ClimateTargetRequest({
        required this.targetC,
    });

    ClimateTargetRequest copyWith({
        double? targetC,
    }) => 
        ClimateTargetRequest(
            targetC: targetC ?? this.targetC,
        );

    factory ClimateTargetRequest.fromJson(Map<String, dynamic> json) => ClimateTargetRequest(
        targetC: json["target_c"]?.toDouble(),
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "target_c": targetC,
    }..removeWhere((_, value) => value == null);
}


///Request body of POST /presentation. Idempotent, starting an active presentation or
///stopping an inactive one returns the current state unchanged.
class PresentationRequest {
    
    ///Start or stop.
    final PresentationAction action;
    
    ///Lecture to attach the presentation to, start only. Defaults to the lecture in progress.
    final String? lectureId;

    PresentationRequest({
        required this.action,
        this.lectureId,
    });

    PresentationRequest copyWith({
        PresentationAction? action,
        String? lectureId,
    }) => 
        PresentationRequest(
            action: action ?? this.action,
            lectureId: lectureId ?? this.lectureId,
        );

    factory PresentationRequest.fromJson(Map<String, dynamic> json) => PresentationRequest(
        action: presentationActionValues.map[json["action"]]!,
        lectureId: json["lecture_id"],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "action": presentationActionValues.reverse[action],
        "lecture_id": lectureId,
    }..removeWhere((_, value) => value == null);
}


///Start or stop.
///
///Presentation request action.
enum PresentationAction {
    START,
    STOP
}

final presentationActionValues = EnumValues({
    "start": PresentationAction.START,
    "stop": PresentationAction.STOP
});


///A prepared announcement the voice stack can speak through the room speaker. The set is
///managed by the API, the app only lists and plays them in this sprint.
class Announcement {
    
    ///Category, used for grouping and icon.
    final AnnouncementCategory category;
    
    ///Expected playback length, seconds, integer 1 to 120.
    final int durationS;
    
    ///Announcement identifier.
    final String id;
    
    ///Spoken language as a BCP 47 tag, for example en-US or uz-UZ.
    final String language;
    
    ///Last playback start, ISO 8601 UTC, null when never played.
    final DateTime? lastPlayedAt;
    
    ///Text that is spoken, 1 to 600 characters.
    final String text;
    
    ///Short title shown in the app, 1 to 80 characters.
    final String title;

    Announcement({
        required this.category,
        required this.durationS,
        required this.id,
        required this.language,
        required this.lastPlayedAt,
        required this.text,
        required this.title,
    });

    Announcement copyWith({
        AnnouncementCategory? category,
        int? durationS,
        String? id,
        String? language,
        DateTime? lastPlayedAt,
        String? text,
        String? title,
    }) => 
        Announcement(
            category: category ?? this.category,
            durationS: durationS ?? this.durationS,
            id: id ?? this.id,
            language: language ?? this.language,
            lastPlayedAt: lastPlayedAt ?? this.lastPlayedAt,
            text: text ?? this.text,
            title: title ?? this.title,
        );

    factory Announcement.fromJson(Map<String, dynamic> json) => Announcement(
        category: announcementCategoryValues.map[json["category"]]!,
        durationS: json["duration_s"],
        id: json["id"],
        language: json["language"],
        lastPlayedAt: json["last_played_at"] == null ? null : DateTime.parse(json["last_played_at"]),
        text: json["text"],
        title: json["title"],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "category": announcementCategoryValues.reverse[category],
        "duration_s": durationS,
        "id": id,
        "language": language,
        "last_played_at": lastPlayedAt?.toIso8601String(),
        "text": text,
        "title": title,
    }..removeWhere((_, value) => value == null);
}


///Category, used for grouping and icon.
///
///Announcement category.
enum AnnouncementCategory {
    EMERGENCY,
    INFO,
    REMINDER,
    SCHEDULE
}

final announcementCategoryValues = EnumValues({
    "emergency": AnnouncementCategory.EMERGENCY,
    "info": AnnouncementCategory.INFO,
    "reminder": AnnouncementCategory.REMINDER,
    "schedule": AnnouncementCategory.SCHEDULE
});


///Optional request body of POST /announcements/{id}/play. An empty object or no body plays
///at the volume from Settings.
class PlayAnnouncementRequest {
    
    ///Volume for this playback only, percent 0 to 100.
    final int? volume;

    PlayAnnouncementRequest({
        this.volume,
    });

    PlayAnnouncementRequest copyWith({
        int? volume,
    }) => 
        PlayAnnouncementRequest(
            volume: volume ?? this.volume,
        );

    factory PlayAnnouncementRequest.fromJson(Map<String, dynamic> json) => PlayAnnouncementRequest(
        volume: json["volume"],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "volume": volume,
    }..removeWhere((_, value) => value == null);
}


///A started announcement playback, returned by POST /announcements/{id}/play. Only one
///playback runs at a time.
class AnnouncementPlayback {
    
    ///Announcement being played.
    final String announcementId;
    
    ///Engine decision that started the playback.
    final String decisionId;
    
    ///started_at plus the announcement duration, ISO 8601 UTC.
    final DateTime expectedEndAt;
    
    ///Playback identifier.
    final String playbackId;
    
    ///Playback start, ISO 8601 UTC.
    final DateTime startedAt;
    
    ///Effective volume, percent 0 to 100.
    final int volume;

    AnnouncementPlayback({
        required this.announcementId,
        required this.decisionId,
        required this.expectedEndAt,
        required this.playbackId,
        required this.startedAt,
        required this.volume,
    });

    AnnouncementPlayback copyWith({
        String? announcementId,
        String? decisionId,
        DateTime? expectedEndAt,
        String? playbackId,
        DateTime? startedAt,
        int? volume,
    }) => 
        AnnouncementPlayback(
            announcementId: announcementId ?? this.announcementId,
            decisionId: decisionId ?? this.decisionId,
            expectedEndAt: expectedEndAt ?? this.expectedEndAt,
            playbackId: playbackId ?? this.playbackId,
            startedAt: startedAt ?? this.startedAt,
            volume: volume ?? this.volume,
        );

    factory AnnouncementPlayback.fromJson(Map<String, dynamic> json) => AnnouncementPlayback(
        announcementId: json["announcement_id"],
        decisionId: json["decision_id"],
        expectedEndAt: DateTime.parse(json["expected_end_at"]),
        playbackId: json["playback_id"],
        startedAt: DateTime.parse(json["started_at"]),
        volume: json["volume"],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "announcement_id": announcementId,
        "decision_id": decisionId,
        "expected_end_at": expectedEndAt.toIso8601String(),
        "playback_id": playbackId,
        "started_at": startedAt.toIso8601String(),
        "volume": volume,
    }..removeWhere((_, value) => value == null);
}


///One decision of the engine: what triggered it, which rule fired, what it did and why.
///Published on decision.action when made, with every command status pending. GET /decisions
///returns the same record with command statuses updated from device.ack. Every
///device.command is issued by exactly one decision.
class Decision {
    
    ///What the engine decided to do.
    final Action action;
    
    ///Device commands issued for this decision, empty when none.
    final List<DecisionCommand> commands;
    
    ///Decision identifier.
    final String id;
    
    ///Result at decision time.
    final DecisionOutcome outcome;
    
    ///Text the voice stack speaks and the app shows as the answer, null when the trigger was
    ///not a request.
    final String? replyText;
    
    ///Correlation id when the trigger was a voice or app request, otherwise null.
    final String? requestId;
    
    ///Identifier of the engine rule that fired, snake_case, for example occupancy_lights_on.
    final String rule;
    
    ///One English sentence explaining the decision, shown in the app.
    final String summary;
    
    ///What caused the decision.
    final DecisionTrigger trigger;
    
    ///Decision time, ISO 8601 UTC.
    final DateTime ts;

    Decision({
        required this.action,
        required this.commands,
        required this.id,
        required this.outcome,
        required this.replyText,
        required this.requestId,
        required this.rule,
        required this.summary,
        required this.trigger,
        required this.ts,
    });

    Decision copyWith({
        Action? action,
        List<DecisionCommand>? commands,
        String? id,
        DecisionOutcome? outcome,
        String? replyText,
        String? requestId,
        String? rule,
        String? summary,
        DecisionTrigger? trigger,
        DateTime? ts,
    }) => 
        Decision(
            action: action ?? this.action,
            commands: commands ?? this.commands,
            id: id ?? this.id,
            outcome: outcome ?? this.outcome,
            replyText: replyText ?? this.replyText,
            requestId: requestId ?? this.requestId,
            rule: rule ?? this.rule,
            summary: summary ?? this.summary,
            trigger: trigger ?? this.trigger,
            ts: ts ?? this.ts,
        );

    factory Decision.fromJson(Map<String, dynamic> json) => Decision(
        action: Action.fromJson(json["action"]),
        commands: List<DecisionCommand>.from(json["commands"].map((x) => DecisionCommand.fromJson(x))),
        id: json["id"],
        outcome: decisionOutcomeValues.map[json["outcome"]]!,
        replyText: json["reply_text"],
        requestId: json["request_id"],
        rule: json["rule"],
        summary: json["summary"],
        trigger: DecisionTrigger.fromJson(json["trigger"]),
        ts: DateTime.parse(json["ts"]),
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "action": action.toJson(),
        "commands": List<dynamic>.from(commands.map((x) => x.toJson())),
        "id": id,
        "outcome": decisionOutcomeValues.reverse[outcome],
        "reply_text": replyText,
        "request_id": requestId,
        "rule": rule,
        "summary": summary,
        "trigger": trigger.toJson(),
        "ts": ts.toIso8601String(),
    }..removeWhere((_, value) => value == null);
}


///What the engine decided to do.
///
///A single change the engine is asked to make or has decided to make.
class Action {
    
    ///Action parameters, an empty object when the action has none.
    final ActionParams params;
    
    ///Action type.
    final ActionType type;
    
    ///Zone the action applies to, null when it applies to the whole room.
    final Zone? zone;

    Action({
        required this.params,
        required this.type,
        required this.zone,
    });

    Action copyWith({
        ActionParams? params,
        ActionType? type,
        Zone? zone,
    }) => 
        Action(
            params: params ?? this.params,
            type: type ?? this.type,
            zone: zone ?? this.zone,
        );

    factory Action.fromJson(Map<String, dynamic> json) => Action(
        params: ActionParams.fromJson(json["params"]),
        type: actionTypeValues.map[json["type"]]!,
        zone: zoneValues.map[json["zone"]],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "params": params.toJson(),
        "type": actionTypeValues.reverse[type],
        "zone": zoneValues.reverse[zone],
    }..removeWhere((_, value) => value == null);
}


///Action parameters, an empty object when the action has none.
///
///Parameters of an Action. Which keys are required depends on the action type, see the
///table in bus.md under control.request.
class ActionParams {
    
    ///Announcement for announcement.play.
    final String? announcementId;
    
    ///Brightness for lighting.set_zone, percent 0 to 100.
    final int? brightness;
    
    ///Fade for lighting.set_zone, milliseconds 0 to 5000.
    final int? fadeMs;
    
    ///Lecture for presentation.start.
    final String? lectureId;
    
    ///Lighting mode for lighting.set_mode.
    final LightingMode? mode;
    
    ///Ring pattern for ring.set.
    final RingPattern? pattern;
    
    ///Fan speed for climate.set_fan, percent 0 to 100.
    final int? speed;
    
    ///Target for climate.set_target, degrees Celsius 16.0 to 30.0.
    final double? targetC;
    
    ///Volume for announcement.play, percent 0 to 100.
    final int? volume;

    ActionParams({
        this.announcementId,
        this.brightness,
        this.fadeMs,
        this.lectureId,
        this.mode,
        this.pattern,
        this.speed,
        this.targetC,
        this.volume,
    });

    ActionParams copyWith({
        String? announcementId,
        int? brightness,
        int? fadeMs,
        String? lectureId,
        LightingMode? mode,
        RingPattern? pattern,
        int? speed,
        double? targetC,
        int? volume,
    }) => 
        ActionParams(
            announcementId: announcementId ?? this.announcementId,
            brightness: brightness ?? this.brightness,
            fadeMs: fadeMs ?? this.fadeMs,
            lectureId: lectureId ?? this.lectureId,
            mode: mode ?? this.mode,
            pattern: pattern ?? this.pattern,
            speed: speed ?? this.speed,
            targetC: targetC ?? this.targetC,
            volume: volume ?? this.volume,
        );

    factory ActionParams.fromJson(Map<String, dynamic> json) => ActionParams(
        announcementId: json["announcement_id"],
        brightness: json["brightness"],
        fadeMs: json["fade_ms"],
        lectureId: json["lecture_id"],
        mode: lightingModeValues.map[json["mode"]],
        pattern: ringPatternValues.map[json["pattern"]],
        speed: json["speed"],
        targetC: json["target_c"]?.toDouble(),
        volume: json["volume"],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "announcement_id": announcementId,
        "brightness": brightness,
        "fade_ms": fadeMs,
        "lecture_id": lectureId,
        "mode": lightingModeValues.reverse[mode],
        "pattern": ringPatternValues.reverse[pattern],
        "speed": speed,
        "target_c": targetC,
        "volume": volume,
    }..removeWhere((_, value) => value == null);
}


///Ring pattern for ring.set.
///
///LED ring animation. idle is a dim breathe, listening follows the wake word, thinking
///spins while the request is processed, speaking pulses during a spoken reply, success and
///error flash once and fall back to idle.
enum RingPattern {
    ERROR,
    IDLE,
    LISTENING,
    OFF,
    SPEAKING,
    SUCCESS,
    THINKING
}

final ringPatternValues = EnumValues({
    "error": RingPattern.ERROR,
    "idle": RingPattern.IDLE,
    "listening": RingPattern.LISTENING,
    "off": RingPattern.OFF,
    "speaking": RingPattern.SPEAKING,
    "success": RingPattern.SUCCESS,
    "thinking": RingPattern.THINKING
});


///Action type.
///
///What the engine does as the result of a decision. lighting.set_zone with a null zone
///applies to every zone. answer only produces reply_text. none means nothing was done.
enum ActionType {
    ANNOUNCEMENT_PLAY,
    ANSWER,
    CLIMATE_SET_FAN,
    CLIMATE_SET_TARGET,
    LIGHTING_SET_MODE,
    LIGHTING_SET_ZONE,
    NONE,
    PRESENTATION_START,
    PRESENTATION_STOP,
    RING_SET
}

final actionTypeValues = EnumValues({
    "announcement.play": ActionType.ANNOUNCEMENT_PLAY,
    "answer": ActionType.ANSWER,
    "climate.set_fan": ActionType.CLIMATE_SET_FAN,
    "climate.set_target": ActionType.CLIMATE_SET_TARGET,
    "lighting.set_mode": ActionType.LIGHTING_SET_MODE,
    "lighting.set_zone": ActionType.LIGHTING_SET_ZONE,
    "none": ActionType.NONE,
    "presentation.start": ActionType.PRESENTATION_START,
    "presentation.stop": ActionType.PRESENTATION_STOP,
    "ring.set": ActionType.RING_SET
});


///Reference to one device command of a decision.
class DecisionCommand {
    
    ///Command id, matches device.command and device.ack.
    final String cmdId;
    
    ///Delivery status.
    final CommandStatus status;
    
    ///Command type.
    final DeviceCommandType type;

    DecisionCommand({
        required this.cmdId,
        required this.status,
        required this.type,
    });

    DecisionCommand copyWith({
        String? cmdId,
        CommandStatus? status,
        DeviceCommandType? type,
    }) => 
        DecisionCommand(
            cmdId: cmdId ?? this.cmdId,
            status: status ?? this.status,
            type: type ?? this.type,
        );

    factory DecisionCommand.fromJson(Map<String, dynamic> json) => DecisionCommand(
        cmdId: json["cmd_id"],
        status: commandStatusValues.map[json["status"]]!,
        type: deviceCommandTypeValues.map[json["type"]]!,
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "cmd_id": cmdId,
        "status": commandStatusValues.reverse[status],
        "type": deviceCommandTypeValues.reverse[type],
    }..removeWhere((_, value) => value == null);
}


///Delivery status.
///
///Delivery status of a device command. pending until the matching device.ack arrives.
enum CommandStatus {
    APPLIED,
    PENDING,
    REJECTED,
    TIMEOUT
}

final commandStatusValues = EnumValues({
    "applied": CommandStatus.APPLIED,
    "pending": CommandStatus.PENDING,
    "rejected": CommandStatus.REJECTED,
    "timeout": CommandStatus.TIMEOUT
});


///Command type.
///
///Downlink message type.
enum DeviceCommandType {
    PING,
    QUERY,
    SET_FAN,
    SET_RING,
    SET_ZONE_LIGHT
}

final deviceCommandTypeValues = EnumValues({
    "ping": DeviceCommandType.PING,
    "query": DeviceCommandType.QUERY,
    "set_fan": DeviceCommandType.SET_FAN,
    "set_ring": DeviceCommandType.SET_RING,
    "set_zone_light": DeviceCommandType.SET_ZONE_LIGHT
});


///Result at decision time.
///
///issued means commands were sent, answered means a query was answered without commands,
///skipped means the rule fired but the room was already in the wanted state, rejected means
///the request could not be carried out, see summary.
enum DecisionOutcome {
    ANSWERED,
    ISSUED,
    REJECTED,
    SKIPPED
}

final decisionOutcomeValues = EnumValues({
    "answered": DecisionOutcome.ANSWERED,
    "issued": DecisionOutcome.ISSUED,
    "rejected": DecisionOutcome.REJECTED,
    "skipped": DecisionOutcome.SKIPPED
});


///What caused the decision.
///
///Cause of a decision.
class DecisionTrigger {
    
    ///Envelope id of the triggering bus event, null for timer triggers.
    final String? eventId;
    
    ///Bus topic of the triggering event, null for timer triggers.
    final BusTopic? topic;
    
    ///Kind of trigger.
    final DecisionTriggerType type;

    DecisionTrigger({
        required this.eventId,
        required this.topic,
        required this.type,
    });

    DecisionTrigger copyWith({
        String? eventId,
        BusTopic? topic,
        DecisionTriggerType? type,
    }) => 
        DecisionTrigger(
            eventId: eventId ?? this.eventId,
            topic: topic ?? this.topic,
            type: type ?? this.type,
        );

    factory DecisionTrigger.fromJson(Map<String, dynamic> json) => DecisionTrigger(
        eventId: json["event_id"],
        topic: busTopicValues.map[json["topic"]],
        type: decisionTriggerTypeValues.map[json["type"]]!,
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "event_id": eventId,
        "topic": busTopicValues.reverse[topic],
        "type": decisionTriggerTypeValues.reverse[type],
    }..removeWhere((_, value) => value == null);
}


///Logical event bus topic. The full list with publishers, subscribers and rates is in
///bus.md.
///
///Logical topic.
enum BusTopic {
    CONTROL_REQUEST,
    DECISION_ACTION,
    DEVICE_ACK,
    DEVICE_COMMAND,
    DEVICE_STATUS,
    PERCEPTION_OCCUPANCY,
    SCHEDULE_LECTURE,
    SENSOR_ENVIRONMENT,
    SENSOR_PRESENCE,
    SETTINGS_CHANGED,
    SYSTEM_HEARTBEAT,
    SYSTEM_STATE,
    VOICE_INTENT,
    VOICE_TRANSCRIPT,
    VOICE_WAKE
}

final busTopicValues = EnumValues({
    "control.request": BusTopic.CONTROL_REQUEST,
    "decision.action": BusTopic.DECISION_ACTION,
    "device.ack": BusTopic.DEVICE_ACK,
    "device.command": BusTopic.DEVICE_COMMAND,
    "device.status": BusTopic.DEVICE_STATUS,
    "perception.occupancy": BusTopic.PERCEPTION_OCCUPANCY,
    "schedule.lecture": BusTopic.SCHEDULE_LECTURE,
    "sensor.environment": BusTopic.SENSOR_ENVIRONMENT,
    "sensor.presence": BusTopic.SENSOR_PRESENCE,
    "settings.changed": BusTopic.SETTINGS_CHANGED,
    "system.heartbeat": BusTopic.SYSTEM_HEARTBEAT,
    "system.state": BusTopic.SYSTEM_STATE,
    "voice.intent": BusTopic.VOICE_INTENT,
    "voice.transcript": BusTopic.VOICE_TRANSCRIPT,
    "voice.wake": BusTopic.VOICE_WAKE
});


///Kind of trigger.
///
///Kind of trigger. presence, occupancy and environment come from sensor topics, voice from
///voice.intent, api from control.request, schedule from schedule.lecture, timer from an
///engine timer such as the vacancy timeout, device from device.ack or a reconnect.
enum DecisionTriggerType {
    API,
    DEVICE,
    ENVIRONMENT,
    OCCUPANCY,
    PRESENCE,
    SCHEDULE,
    TIMER,
    VOICE
}

final decisionTriggerTypeValues = EnumValues({
    "api": DecisionTriggerType.API,
    "device": DecisionTriggerType.DEVICE,
    "environment": DecisionTriggerType.ENVIRONMENT,
    "occupancy": DecisionTriggerType.OCCUPANCY,
    "presence": DecisionTriggerType.PRESENCE,
    "schedule": DecisionTriggerType.SCHEDULE,
    "timer": DecisionTriggerType.TIMER,
    "voice": DecisionTriggerType.VOICE
});


///Room configuration. Owned by the API, persisted on the Pi, published on settings.changed
///after every change. Every field is always present.
class Settings {
    
    ///Announcement configuration.
    final AnnouncementSettings announcements;
    
    ///Climate configuration.
    final ClimateSettings climate;
    
    ///Lighting configuration.
    final LightingSettings lighting;
    
    ///Room identifier, set at provisioning, read-only.
    final String roomId;
    
    ///Display name, 1 to 60 characters.
    final String roomName;
    
    ///IANA time zone used by the app to display times, for example Asia/Tashkent. Every
    ///timestamp on the wire stays UTC.
    final String timezone;
    
    ///Last change, ISO 8601 UTC.
    final DateTime updatedAt;
    
    ///Voice configuration.
    final VoiceSettings voice;

    Settings({
        required this.announcements,
        required this.climate,
        required this.lighting,
        required this.roomId,
        required this.roomName,
        required this.timezone,
        required this.updatedAt,
        required this.voice,
    });

    Settings copyWith({
        AnnouncementSettings? announcements,
        ClimateSettings? climate,
        LightingSettings? lighting,
        String? roomId,
        String? roomName,
        String? timezone,
        DateTime? updatedAt,
        VoiceSettings? voice,
    }) => 
        Settings(
            announcements: announcements ?? this.announcements,
            climate: climate ?? this.climate,
            lighting: lighting ?? this.lighting,
            roomId: roomId ?? this.roomId,
            roomName: roomName ?? this.roomName,
            timezone: timezone ?? this.timezone,
            updatedAt: updatedAt ?? this.updatedAt,
            voice: voice ?? this.voice,
        );

    factory Settings.fromJson(Map<String, dynamic> json) => Settings(
        announcements: AnnouncementSettings.fromJson(json["announcements"]),
        climate: ClimateSettings.fromJson(json["climate"]),
        lighting: LightingSettings.fromJson(json["lighting"]),
        roomId: json["room_id"],
        roomName: json["room_name"],
        timezone: json["timezone"],
        updatedAt: DateTime.parse(json["updated_at"]),
        voice: VoiceSettings.fromJson(json["voice"]),
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "announcements": announcements.toJson(),
        "climate": climate.toJson(),
        "lighting": lighting.toJson(),
        "room_id": roomId,
        "room_name": roomName,
        "timezone": timezone,
        "updated_at": updatedAt.toIso8601String(),
        "voice": voice.toJson(),
    }..removeWhere((_, value) => value == null);
}


///Announcement configuration.
class AnnouncementSettings {
    
    ///Default playback volume, percent 0 to 100.
    final int volume;

    AnnouncementSettings({
        required this.volume,
    });

    AnnouncementSettings copyWith({
        int? volume,
    }) => 
        AnnouncementSettings(
            volume: volume ?? this.volume,
        );

    factory AnnouncementSettings.fromJson(Map<String, dynamic> json) => AnnouncementSettings(
        volume: json["volume"],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "volume": volume,
    }..removeWhere((_, value) => value == null);
}


///Climate configuration.
class ClimateSettings {
    
    ///Target restored at the start of each lecture, degrees Celsius, one decimal place.
    final double defaultTargetC;
    
    ///Dead band around the target, degrees Celsius, one decimal place, 0.1 to 3.0.
    final double hysteresisC;

    ClimateSettings({
        required this.defaultTargetC,
        required this.hysteresisC,
    });

    ClimateSettings copyWith({
        double? defaultTargetC,
        double? hysteresisC,
    }) => 
        ClimateSettings(
            defaultTargetC: defaultTargetC ?? this.defaultTargetC,
            hysteresisC: hysteresisC ?? this.hysteresisC,
        );

    factory ClimateSettings.fromJson(Map<String, dynamic> json) => ClimateSettings(
        defaultTargetC: json["default_target_c"]?.toDouble(),
        hysteresisC: json["hysteresis_c"]?.toDouble(),
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "default_target_c": defaultTargetC,
        "hysteresis_c": hysteresisC,
    }..removeWhere((_, value) => value == null);
}


///Lighting configuration.
class LightingSettings {
    
    ///When true, auto mode lowers brightness as ambient lux rises above target_lux.
    final bool daylightHarvesting;
    
    ///Per-mode brightness presets.
    final LightingPresets presets;
    
    ///Auto mode lights occupied zones only while ambient light is below this, lux.
    final int targetLux;
    
    ///Seconds a zone must be unoccupied before auto mode dims it, integer 30 to 3600.
    final int vacancyTimeoutS;
    
    ///Brightness of unoccupied zones in auto mode while a lecture is in progress, percent 0 to
    ///100. Outside lectures unoccupied zones are off.
    final int vacantBrightness;

    LightingSettings({
        required this.daylightHarvesting,
        required this.presets,
        required this.targetLux,
        required this.vacancyTimeoutS,
        required this.vacantBrightness,
    });

    LightingSettings copyWith({
        bool? daylightHarvesting,
        LightingPresets? presets,
        int? targetLux,
        int? vacancyTimeoutS,
        int? vacantBrightness,
    }) => 
        LightingSettings(
            daylightHarvesting: daylightHarvesting ?? this.daylightHarvesting,
            presets: presets ?? this.presets,
            targetLux: targetLux ?? this.targetLux,
            vacancyTimeoutS: vacancyTimeoutS ?? this.vacancyTimeoutS,
            vacantBrightness: vacantBrightness ?? this.vacantBrightness,
        );

    factory LightingSettings.fromJson(Map<String, dynamic> json) => LightingSettings(
        daylightHarvesting: json["daylight_harvesting"],
        presets: LightingPresets.fromJson(json["presets"]),
        targetLux: json["target_lux"],
        vacancyTimeoutS: json["vacancy_timeout_s"],
        vacantBrightness: json["vacant_brightness"],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "daylight_harvesting": daylightHarvesting,
        "presets": presets.toJson(),
        "target_lux": targetLux,
        "vacancy_timeout_s": vacancyTimeoutS,
        "vacant_brightness": vacantBrightness,
    }..removeWhere((_, value) => value == null);
}


///Per-mode brightness presets.
///
///Brightness per zone for the lecture and presentation modes.
class LightingPresets {
    
    ///Applied in lecture mode, percent 0 to 100 per zone.
    final ZoneLevels lecture;
    
    ///Applied in presentation mode, percent 0 to 100 per zone.
    final ZoneLevels presentation;

    LightingPresets({
        required this.lecture,
        required this.presentation,
    });

    LightingPresets copyWith({
        ZoneLevels? lecture,
        ZoneLevels? presentation,
    }) => 
        LightingPresets(
            lecture: lecture ?? this.lecture,
            presentation: presentation ?? this.presentation,
        );

    factory LightingPresets.fromJson(Map<String, dynamic> json) => LightingPresets(
        lecture: ZoneLevels.fromJson(json["lecture"]),
        presentation: ZoneLevels.fromJson(json["presentation"]),
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "lecture": lecture.toJson(),
        "presentation": presentation.toJson(),
    }..removeWhere((_, value) => value == null);
}


///Applied in lecture mode, percent 0 to 100 per zone.
///
///One brightness value per zone, in percent 0 to 100.
///
///Applied in presentation mode, percent 0 to 100 per zone.
class ZoneLevels {
    
    ///Center zone brightness, percent 0 to 100.
    final int center;
    
    ///Left zone brightness, percent 0 to 100.
    final int left;
    
    ///Professor zone brightness, percent 0 to 100.
    final int professor;
    
    ///Right zone brightness, percent 0 to 100.
    final int right;

    ZoneLevels({
        required this.center,
        required this.left,
        required this.professor,
        required this.right,
    });

    ZoneLevels copyWith({
        int? center,
        int? left,
        int? professor,
        int? right,
    }) => 
        ZoneLevels(
            center: center ?? this.center,
            left: left ?? this.left,
            professor: professor ?? this.professor,
            right: right ?? this.right,
        );

    factory ZoneLevels.fromJson(Map<String, dynamic> json) => ZoneLevels(
        center: json["center"],
        left: json["left"],
        professor: json["professor"],
        right: json["right"],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "center": center,
        "left": left,
        "professor": professor,
        "right": right,
    }..removeWhere((_, value) => value == null);
}


///Voice configuration.
class VoiceSettings {
    
    ///When false the voice stack ignores the wake word. POST /voice/ask keeps working.
    final bool enabled;
    
    ///Recognition language as a BCP 47 tag.
    final String language;
    
    ///Intents below this confidence are turned into unknown, 0.0 to 1.0.
    final double minConfidence;
    
    ///Wake word model name.
    final String wakeWord;

    VoiceSettings({
        required this.enabled,
        required this.language,
        required this.minConfidence,
        required this.wakeWord,
    });

    VoiceSettings copyWith({
        bool? enabled,
        String? language,
        double? minConfidence,
        String? wakeWord,
    }) => 
        VoiceSettings(
            enabled: enabled ?? this.enabled,
            language: language ?? this.language,
            minConfidence: minConfidence ?? this.minConfidence,
            wakeWord: wakeWord ?? this.wakeWord,
        );

    factory VoiceSettings.fromJson(Map<String, dynamic> json) => VoiceSettings(
        enabled: json["enabled"],
        language: json["language"],
        minConfidence: json["min_confidence"]?.toDouble(),
        wakeWord: json["wake_word"],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "enabled": enabled,
        "language": language,
        "min_confidence": minConfidence,
        "wake_word": wakeWord,
    }..removeWhere((_, value) => value == null);
}


///Request body of PATCH /settings. Deep merge semantics, only the keys present are changed,
///nested objects are merged key by key. room_id and updated_at are read-only and not
///accepted. The response is the full Settings after the merge.
class SettingsPatch {
    
    ///Announcement changes.
    final AnnouncementSettingsPatch? announcements;
    
    ///Climate changes.
    final ClimateSettingsPatch? climate;
    
    ///Lighting changes.
    final LightingSettingsPatch? lighting;
    
    ///Display name, 1 to 60 characters.
    final String? roomName;
    
    ///IANA time zone.
    final String? timezone;
    
    ///Voice changes.
    final VoiceSettingsPatch? voice;

    SettingsPatch({
        this.announcements,
        this.climate,
        this.lighting,
        this.roomName,
        this.timezone,
        this.voice,
    });

    SettingsPatch copyWith({
        AnnouncementSettingsPatch? announcements,
        ClimateSettingsPatch? climate,
        LightingSettingsPatch? lighting,
        String? roomName,
        String? timezone,
        VoiceSettingsPatch? voice,
    }) => 
        SettingsPatch(
            announcements: announcements ?? this.announcements,
            climate: climate ?? this.climate,
            lighting: lighting ?? this.lighting,
            roomName: roomName ?? this.roomName,
            timezone: timezone ?? this.timezone,
            voice: voice ?? this.voice,
        );

    factory SettingsPatch.fromJson(Map<String, dynamic> json) => SettingsPatch(
        announcements: json["announcements"] == null ? null : AnnouncementSettingsPatch.fromJson(json["announcements"]),
        climate: json["climate"] == null ? null : ClimateSettingsPatch.fromJson(json["climate"]),
        lighting: json["lighting"] == null ? null : LightingSettingsPatch.fromJson(json["lighting"]),
        roomName: json["room_name"],
        timezone: json["timezone"],
        voice: json["voice"] == null ? null : VoiceSettingsPatch.fromJson(json["voice"]),
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "announcements": announcements?.toJson(),
        "climate": climate?.toJson(),
        "lighting": lighting?.toJson(),
        "room_name": roomName,
        "timezone": timezone,
        "voice": voice?.toJson(),
    }..removeWhere((_, value) => value == null);
}


///Announcement changes.
///
///Partial AnnouncementSettings.
class AnnouncementSettingsPatch {
    
    ///Percent 0 to 100.
    final int? volume;

    AnnouncementSettingsPatch({
        this.volume,
    });

    AnnouncementSettingsPatch copyWith({
        int? volume,
    }) => 
        AnnouncementSettingsPatch(
            volume: volume ?? this.volume,
        );

    factory AnnouncementSettingsPatch.fromJson(Map<String, dynamic> json) => AnnouncementSettingsPatch(
        volume: json["volume"],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "volume": volume,
    }..removeWhere((_, value) => value == null);
}


///Climate changes.
///
///Partial ClimateSettings.
class ClimateSettingsPatch {
    
    ///Degrees Celsius, one decimal place, 16.0 to 30.0.
    final double? defaultTargetC;
    
    ///Degrees Celsius, one decimal place, 0.1 to 3.0.
    final double? hysteresisC;

    ClimateSettingsPatch({
        this.defaultTargetC,
        this.hysteresisC,
    });

    ClimateSettingsPatch copyWith({
        double? defaultTargetC,
        double? hysteresisC,
    }) => 
        ClimateSettingsPatch(
            defaultTargetC: defaultTargetC ?? this.defaultTargetC,
            hysteresisC: hysteresisC ?? this.hysteresisC,
        );

    factory ClimateSettingsPatch.fromJson(Map<String, dynamic> json) => ClimateSettingsPatch(
        defaultTargetC: json["default_target_c"]?.toDouble(),
        hysteresisC: json["hysteresis_c"]?.toDouble(),
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "default_target_c": defaultTargetC,
        "hysteresis_c": hysteresisC,
    }..removeWhere((_, value) => value == null);
}


///Lighting changes.
///
///Partial LightingSettings.
class LightingSettingsPatch {
    
    ///Daylight harvesting on or off.
    final bool? daylightHarvesting;
    
    ///Preset changes.
    final LightingPresetsPatch? presets;
    
    ///Lux.
    final int? targetLux;
    
    ///Seconds, integer 30 to 3600.
    final int? vacancyTimeoutS;
    
    ///Percent 0 to 100.
    final int? vacantBrightness;

    LightingSettingsPatch({
        this.daylightHarvesting,
        this.presets,
        this.targetLux,
        this.vacancyTimeoutS,
        this.vacantBrightness,
    });

    LightingSettingsPatch copyWith({
        bool? daylightHarvesting,
        LightingPresetsPatch? presets,
        int? targetLux,
        int? vacancyTimeoutS,
        int? vacantBrightness,
    }) => 
        LightingSettingsPatch(
            daylightHarvesting: daylightHarvesting ?? this.daylightHarvesting,
            presets: presets ?? this.presets,
            targetLux: targetLux ?? this.targetLux,
            vacancyTimeoutS: vacancyTimeoutS ?? this.vacancyTimeoutS,
            vacantBrightness: vacantBrightness ?? this.vacantBrightness,
        );

    factory LightingSettingsPatch.fromJson(Map<String, dynamic> json) => LightingSettingsPatch(
        daylightHarvesting: json["daylight_harvesting"],
        presets: json["presets"] == null ? null : LightingPresetsPatch.fromJson(json["presets"]),
        targetLux: json["target_lux"],
        vacancyTimeoutS: json["vacancy_timeout_s"],
        vacantBrightness: json["vacant_brightness"],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "daylight_harvesting": daylightHarvesting,
        "presets": presets?.toJson(),
        "target_lux": targetLux,
        "vacancy_timeout_s": vacancyTimeoutS,
        "vacant_brightness": vacantBrightness,
    }..removeWhere((_, value) => value == null);
}


///Preset changes.
///
///Partial LightingPresets.
class LightingPresetsPatch {
    
    ///Lecture preset changes.
    final ZoneLevelsPatch? lecture;
    
    ///Presentation preset changes.
    final ZoneLevelsPatch? presentation;

    LightingPresetsPatch({
        this.lecture,
        this.presentation,
    });

    LightingPresetsPatch copyWith({
        ZoneLevelsPatch? lecture,
        ZoneLevelsPatch? presentation,
    }) => 
        LightingPresetsPatch(
            lecture: lecture ?? this.lecture,
            presentation: presentation ?? this.presentation,
        );

    factory LightingPresetsPatch.fromJson(Map<String, dynamic> json) => LightingPresetsPatch(
        lecture: json["lecture"] == null ? null : ZoneLevelsPatch.fromJson(json["lecture"]),
        presentation: json["presentation"] == null ? null : ZoneLevelsPatch.fromJson(json["presentation"]),
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "lecture": lecture?.toJson(),
        "presentation": presentation?.toJson(),
    }..removeWhere((_, value) => value == null);
}


///Lecture preset changes.
///
///Partial ZoneLevels, percent 0 to 100 per zone.
///
///Presentation preset changes.
class ZoneLevelsPatch {
    
    ///Percent 0 to 100.
    final int? center;
    
    ///Percent 0 to 100.
    final int? left;
    
    ///Percent 0 to 100.
    final int? professor;
    
    ///Percent 0 to 100.
    final int? right;

    ZoneLevelsPatch({
        this.center,
        this.left,
        this.professor,
        this.right,
    });

    ZoneLevelsPatch copyWith({
        int? center,
        int? left,
        int? professor,
        int? right,
    }) => 
        ZoneLevelsPatch(
            center: center ?? this.center,
            left: left ?? this.left,
            professor: professor ?? this.professor,
            right: right ?? this.right,
        );

    factory ZoneLevelsPatch.fromJson(Map<String, dynamic> json) => ZoneLevelsPatch(
        center: json["center"],
        left: json["left"],
        professor: json["professor"],
        right: json["right"],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "center": center,
        "left": left,
        "professor": professor,
        "right": right,
    }..removeWhere((_, value) => value == null);
}


///Voice changes.
///
///Partial VoiceSettings.
class VoiceSettingsPatch {
    
    ///Wake word on or off.
    final bool? enabled;
    
    ///BCP 47 tag.
    final String? language;
    
    ///0.0 to 1.0.
    final double? minConfidence;
    
    ///Wake word model name.
    final String? wakeWord;

    VoiceSettingsPatch({
        this.enabled,
        this.language,
        this.minConfidence,
        this.wakeWord,
    });

    VoiceSettingsPatch copyWith({
        bool? enabled,
        String? language,
        double? minConfidence,
        String? wakeWord,
    }) => 
        VoiceSettingsPatch(
            enabled: enabled ?? this.enabled,
            language: language ?? this.language,
            minConfidence: minConfidence ?? this.minConfidence,
            wakeWord: wakeWord ?? this.wakeWord,
        );

    factory VoiceSettingsPatch.fromJson(Map<String, dynamic> json) => VoiceSettingsPatch(
        enabled: json["enabled"],
        language: json["language"],
        minConfidence: json["min_confidence"]?.toDouble(),
        wakeWord: json["wake_word"],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "enabled": enabled,
        "language": language,
        "min_confidence": minConfidence,
        "wake_word": wakeWord,
    }..removeWhere((_, value) => value == null);
}


///Time series of one metric, bucketed by the recorder and returned by GET /history. Each
///point is the mean over its bucket, ts is the bucket start. A bucket without samples has
///value null. Points are in ascending ts order with no gaps.
class HistorySeries {
    
    ///Bucket width, seconds.
    final int bucketS;
    
    ///Inclusive start, ISO 8601 UTC, aligned to bucket_s.
    final DateTime from;
    
    ///Metric.
    final HistoryMetric metric;
    
    ///Data points, at most 1440.
    final List<HistoryPoint> points;
    
    ///Exclusive end, ISO 8601 UTC, aligned to bucket_s.
    final DateTime to;
    
    ///Unit of every value.
    final HistoryUnit unit;
    
    ///Zone for occupancy, null for the whole room and for other metrics.
    final Zone? zone;

    HistorySeries({
        required this.bucketS,
        required this.from,
        required this.metric,
        required this.points,
        required this.to,
        required this.unit,
        required this.zone,
    });

    HistorySeries copyWith({
        int? bucketS,
        DateTime? from,
        HistoryMetric? metric,
        List<HistoryPoint>? points,
        DateTime? to,
        HistoryUnit? unit,
        Zone? zone,
    }) => 
        HistorySeries(
            bucketS: bucketS ?? this.bucketS,
            from: from ?? this.from,
            metric: metric ?? this.metric,
            points: points ?? this.points,
            to: to ?? this.to,
            unit: unit ?? this.unit,
            zone: zone ?? this.zone,
        );

    factory HistorySeries.fromJson(Map<String, dynamic> json) => HistorySeries(
        bucketS: json["bucket_s"],
        from: DateTime.parse(json["from"]),
        metric: historyMetricValues.map[json["metric"]]!,
        points: List<HistoryPoint>.from(json["points"].map((x) => HistoryPoint.fromJson(x))),
        to: DateTime.parse(json["to"]),
        unit: historyUnitValues.map[json["unit"]]!,
        zone: zoneValues.map[json["zone"]],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "bucket_s": bucketS,
        "from": from.toIso8601String(),
        "metric": historyMetricValues.reverse[metric],
        "points": List<dynamic>.from(points.map((x) => x.toJson())),
        "to": to.toIso8601String(),
        "unit": historyUnitValues.reverse[unit],
        "zone": zoneValues.reverse[zone],
    }..removeWhere((_, value) => value == null);
}


///Metric.
///
///temperature_c in celsius, humidity_pct in percent, lux in lux, occupancy in people.
enum HistoryMetric {
    HUMIDITY_PCT,
    LUX,
    OCCUPANCY,
    TEMPERATURE_C
}

final historyMetricValues = EnumValues({
    "humidity_pct": HistoryMetric.HUMIDITY_PCT,
    "lux": HistoryMetric.LUX,
    "occupancy": HistoryMetric.OCCUPANCY,
    "temperature_c": HistoryMetric.TEMPERATURE_C
});


///One bucket.
class HistoryPoint {
    
    ///Bucket start, ISO 8601 UTC.
    final DateTime ts;
    
    ///Bucket mean in the series unit, rounded to one decimal place, null when the bucket is
    ///empty.
    final double? value;

    HistoryPoint({
        required this.ts,
        required this.value,
    });

    HistoryPoint copyWith({
        DateTime? ts,
        double? value,
    }) => 
        HistoryPoint(
            ts: ts ?? this.ts,
            value: value ?? this.value,
        );

    factory HistoryPoint.fromJson(Map<String, dynamic> json) => HistoryPoint(
        ts: DateTime.parse(json["ts"]),
        value: json["value"]?.toDouble(),
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "ts": ts.toIso8601String(),
        "value": value,
    }..removeWhere((_, value) => value == null);
}


///Unit of every value.
///
///Unit of a history value.
enum HistoryUnit {
    CELSIUS,
    LUX,
    PEOPLE,
    PERCENT
}

final historyUnitValues = EnumValues({
    "celsius": HistoryUnit.CELSIUS,
    "lux": HistoryUnit.LUX,
    "people": HistoryUnit.PEOPLE,
    "percent": HistoryUnit.PERCENT
});


///Request body of POST /voice/ask. Text typed or dictated in the app, handled exactly like
///a spoken request.
class VoiceAskRequest {
    
    ///Optional client supplied correlation id. The API generates one when absent.
    final String? requestId;
    
    ///Request text, 1 to 200 characters.
    final String text;

    VoiceAskRequest({
        this.requestId,
        required this.text,
    });

    VoiceAskRequest copyWith({
        String? requestId,
        String? text,
    }) => 
        VoiceAskRequest(
            requestId: requestId ?? this.requestId,
            text: text ?? this.text,
        );

    factory VoiceAskRequest.fromJson(Map<String, dynamic> json) => VoiceAskRequest(
        requestId: json["request_id"],
        text: json["text"],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "request_id": requestId,
        "text": text,
    }..removeWhere((_, value) => value == null);
}


///Result of POST /voice/ask, returned once the engine decided and, for device actions, once
///every command was acknowledged or timed out.
class VoiceAskResponse {
    
    ///How the text was understood.
    final VoiceCommand command;
    
    ///Engine decision, null when the intent was unknown.
    final String? decisionId;
    
    ///What happened.
    final VoiceAskOutcome outcome;
    
    ///Answer to show to the user.
    final String replyText;
    
    ///Correlation id of the request.
    final String requestId;

    VoiceAskResponse({
        required this.command,
        required this.decisionId,
        required this.outcome,
        required this.replyText,
        required this.requestId,
    });

    VoiceAskResponse copyWith({
        VoiceCommand? command,
        String? decisionId,
        VoiceAskOutcome? outcome,
        String? replyText,
        String? requestId,
    }) => 
        VoiceAskResponse(
            command: command ?? this.command,
            decisionId: decisionId ?? this.decisionId,
            outcome: outcome ?? this.outcome,
            replyText: replyText ?? this.replyText,
            requestId: requestId ?? this.requestId,
        );

    factory VoiceAskResponse.fromJson(Map<String, dynamic> json) => VoiceAskResponse(
        command: VoiceCommand.fromJson(json["command"]),
        decisionId: json["decision_id"],
        outcome: voiceAskOutcomeValues.map[json["outcome"]]!,
        replyText: json["reply_text"],
        requestId: json["request_id"],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "command": command.toJson(),
        "decision_id": decisionId,
        "outcome": voiceAskOutcomeValues.reverse[outcome],
        "reply_text": replyText,
        "request_id": requestId,
    }..removeWhere((_, value) => value == null);
}


///How the text was understood.
///
///Parsed user request handed from the voice stack to the engine on voice.intent. The intent
///set is closed for this sprint. Each intent fixes action and target.type as in the table
///in bus.md, enforced by the allOf rules below. Intents below Settings.voice.min_confidence
///are emitted as unknown.
class VoiceCommand {
    
    ///Verb implied by the intent.
    final VoiceAction action;
    
    ///Intent confidence, 0.0 to 1.0.
    final double confidence;
    
    ///Recognised intent.
    final VoiceIntent intent;
    
    ///Intent parameters, an empty object when the intent has none.
    final VoiceParams params;
    
    ///Transcript the intent was parsed from, at most 200 characters.
    final String rawText;
    
    ///Correlation id, same as on voice.wake and voice.transcript.
    final String requestId;
    
    ///Where the text came from.
    final VoiceSource source;
    
    ///What the intent acts on.
    final VoiceTarget target;

    VoiceCommand({
        required this.action,
        required this.confidence,
        required this.intent,
        required this.params,
        required this.rawText,
        required this.requestId,
        required this.source,
        required this.target,
    });

    VoiceCommand copyWith({
        VoiceAction? action,
        double? confidence,
        VoiceIntent? intent,
        VoiceParams? params,
        String? rawText,
        String? requestId,
        VoiceSource? source,
        VoiceTarget? target,
    }) => 
        VoiceCommand(
            action: action ?? this.action,
            confidence: confidence ?? this.confidence,
            intent: intent ?? this.intent,
            params: params ?? this.params,
            rawText: rawText ?? this.rawText,
            requestId: requestId ?? this.requestId,
            source: source ?? this.source,
            target: target ?? this.target,
        );

    factory VoiceCommand.fromJson(Map<String, dynamic> json) => VoiceCommand(
        action: voiceActionValues.map[json["action"]]!,
        confidence: json["confidence"]?.toDouble(),
        intent: voiceIntentValues.map[json["intent"]]!,
        params: VoiceParams.fromJson(json["params"]),
        rawText: json["raw_text"],
        requestId: json["request_id"],
        source: voiceSourceValues.map[json["source"]]!,
        target: VoiceTarget.fromJson(json["target"]),
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "action": voiceActionValues.reverse[action],
        "confidence": confidence,
        "intent": voiceIntentValues.reverse[intent],
        "params": params.toJson(),
        "raw_text": rawText,
        "request_id": requestId,
        "source": voiceSourceValues.reverse[source],
        "target": target.toJson(),
    }..removeWhere((_, value) => value == null);
}


///Verb implied by the intent.
///
///Verb of an intent.
enum VoiceAction {
    NONE,
    PLAY,
    QUERY,
    SET,
    START,
    STOP
}

final voiceActionValues = EnumValues({
    "none": VoiceAction.NONE,
    "play": VoiceAction.PLAY,
    "query": VoiceAction.QUERY,
    "set": VoiceAction.SET,
    "start": VoiceAction.START,
    "stop": VoiceAction.STOP
});


///Recognised intent.
///
///Intents supported in this sprint, nothing more.
enum VoiceIntent {
    LIGHTS_OFF,
    LIGHTS_ON,
    PLAY_ANNOUNCEMENT,
    QUERY_HUMIDITY,
    QUERY_LECTURE,
    QUERY_OCCUPANCY,
    QUERY_TEMPERATURE,
    SET_BRIGHTNESS,
    START_PRESENTATION,
    STOP_PRESENTATION,
    UNKNOWN
}

final voiceIntentValues = EnumValues({
    "lights_off": VoiceIntent.LIGHTS_OFF,
    "lights_on": VoiceIntent.LIGHTS_ON,
    "play_announcement": VoiceIntent.PLAY_ANNOUNCEMENT,
    "query_humidity": VoiceIntent.QUERY_HUMIDITY,
    "query_lecture": VoiceIntent.QUERY_LECTURE,
    "query_occupancy": VoiceIntent.QUERY_OCCUPANCY,
    "query_temperature": VoiceIntent.QUERY_TEMPERATURE,
    "set_brightness": VoiceIntent.SET_BRIGHTNESS,
    "start_presentation": VoiceIntent.START_PRESENTATION,
    "stop_presentation": VoiceIntent.STOP_PRESENTATION,
    "unknown": VoiceIntent.UNKNOWN
});


///Intent parameters, an empty object when the intent has none.
///
///Intent parameters. brightness is required for set_brightness, announcement_id for
///play_announcement.
class VoiceParams {
    
    ///Announcement matched from the utterance.
    final String? announcementId;
    
    ///Requested brightness, percent 0 to 100.
    final int? brightness;

    VoiceParams({
        this.announcementId,
        this.brightness,
    });

    VoiceParams copyWith({
        String? announcementId,
        int? brightness,
    }) => 
        VoiceParams(
            announcementId: announcementId ?? this.announcementId,
            brightness: brightness ?? this.brightness,
        );

    factory VoiceParams.fromJson(Map<String, dynamic> json) => VoiceParams(
        announcementId: json["announcement_id"],
        brightness: json["brightness"],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "announcement_id": announcementId,
        "brightness": brightness,
    }..removeWhere((_, value) => value == null);
}


///Where the text came from.
///
///microphone is the room microphone, app is text sent through POST /voice/ask.
///
///microphone or app.
enum VoiceSource {
    APP,
    MICROPHONE
}

final voiceSourceValues = EnumValues({
    "app": VoiceSource.APP,
    "microphone": VoiceSource.MICROPHONE
});


///What the intent acts on.
///
///Target of an intent.
class VoiceTarget {
    
    ///Subsystem.
    final VoiceTargetType type;
    
    ///Zone named in the utterance, null for the whole room.
    final Zone? zone;

    VoiceTarget({
        required this.type,
        required this.zone,
    });

    VoiceTarget copyWith({
        VoiceTargetType? type,
        Zone? zone,
    }) => 
        VoiceTarget(
            type: type ?? this.type,
            zone: zone ?? this.zone,
        );

    factory VoiceTarget.fromJson(Map<String, dynamic> json) => VoiceTarget(
        type: voiceTargetTypeValues.map[json["type"]]!,
        zone: zoneValues.map[json["zone"]],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "type": voiceTargetTypeValues.reverse[type],
        "zone": zoneValues.reverse[zone],
    }..removeWhere((_, value) => value == null);
}


///Subsystem.
///
///Subsystem an intent acts on.
enum VoiceTargetType {
    ANNOUNCEMENT,
    CLIMATE,
    LECTURE,
    LIGHTING,
    NONE,
    OCCUPANCY,
    PRESENTATION
}

final voiceTargetTypeValues = EnumValues({
    "announcement": VoiceTargetType.ANNOUNCEMENT,
    "climate": VoiceTargetType.CLIMATE,
    "lecture": VoiceTargetType.LECTURE,
    "lighting": VoiceTargetType.LIGHTING,
    "none": VoiceTargetType.NONE,
    "occupancy": VoiceTargetType.OCCUPANCY,
    "presentation": VoiceTargetType.PRESENTATION
});


///What happened.
///
///executed means device commands were applied, answered means a query was answered,
///rejected means the engine could not carry it out, for example with the device offline,
///not_understood means the intent was unknown.
enum VoiceAskOutcome {
    ANSWERED,
    EXECUTED,
    NOT_UNDERSTOOD,
    REJECTED
}

final voiceAskOutcomeValues = EnumValues({
    "answered": VoiceAskOutcome.ANSWERED,
    "executed": VoiceAskOutcome.EXECUTED,
    "not_understood": VoiceAskOutcome.NOT_UNDERSTOOD,
    "rejected": VoiceAskOutcome.REJECTED
});


///Wake word detected by the voice stack, published on voice.wake. Opens a request and
///assigns the request_id that follows it to the reply.
class VoiceWake {
    
    ///Detector score, 0.0 to 1.0.
    final double confidence;
    
    ///New correlation id for this request.
    final String requestId;
    
    ///Detection time, ISO 8601 UTC.
    final DateTime ts;
    
    ///Wake word model that fired.
    final String wakeWord;

    VoiceWake({
        required this.confidence,
        required this.requestId,
        required this.ts,
        required this.wakeWord,
    });

    VoiceWake copyWith({
        double? confidence,
        String? requestId,
        DateTime? ts,
        String? wakeWord,
    }) => 
        VoiceWake(
            confidence: confidence ?? this.confidence,
            requestId: requestId ?? this.requestId,
            ts: ts ?? this.ts,
            wakeWord: wakeWord ?? this.wakeWord,
        );

    factory VoiceWake.fromJson(Map<String, dynamic> json) => VoiceWake(
        confidence: json["confidence"]?.toDouble(),
        requestId: json["request_id"],
        ts: DateTime.parse(json["ts"]),
        wakeWord: json["wake_word"],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "confidence": confidence,
        "request_id": requestId,
        "ts": ts.toIso8601String(),
        "wake_word": wakeWord,
    }..removeWhere((_, value) => value == null);
}


///Final transcript of one utterance, published on voice.transcript. Partial transcripts are
///never published. The API publishes one with source app for every POST /voice/ask so both
///paths reach the same parser.
class VoiceTranscript {
    
    ///Recogniser confidence, 0.0 to 1.0, 1.0 for source app.
    final double confidence;
    
    ///Audio length, milliseconds, integer 0 to 30000, null for source app.
    final int? durationMs;
    
    ///Recognition language as a BCP 47 tag.
    final String language;
    
    ///Correlation id of the request.
    final String requestId;
    
    ///microphone or app.
    final VoiceSource source;
    
    ///Recognised text, at most 200 characters, empty when nothing was understood.
    final String text;
    
    ///End of utterance, or API receipt time for source app, ISO 8601 UTC.
    final DateTime ts;

    VoiceTranscript({
        required this.confidence,
        required this.durationMs,
        required this.language,
        required this.requestId,
        required this.source,
        required this.text,
        required this.ts,
    });

    VoiceTranscript copyWith({
        double? confidence,
        int? durationMs,
        String? language,
        String? requestId,
        VoiceSource? source,
        String? text,
        DateTime? ts,
    }) => 
        VoiceTranscript(
            confidence: confidence ?? this.confidence,
            durationMs: durationMs ?? this.durationMs,
            language: language ?? this.language,
            requestId: requestId ?? this.requestId,
            source: source ?? this.source,
            text: text ?? this.text,
            ts: ts ?? this.ts,
        );

    factory VoiceTranscript.fromJson(Map<String, dynamic> json) => VoiceTranscript(
        confidence: json["confidence"]?.toDouble(),
        durationMs: json["duration_ms"],
        language: json["language"],
        requestId: json["request_id"],
        source: voiceSourceValues.map[json["source"]]!,
        text: json["text"],
        ts: DateTime.parse(json["ts"]),
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "confidence": confidence,
        "duration_ms": durationMs,
        "language": language,
        "request_id": requestId,
        "source": voiceSourceValues.reverse[source],
        "text": text,
        "ts": ts.toIso8601String(),
    }..removeWhere((_, value) => value == null);
}


///Liveness signal every Pi component publishes on system.heartbeat every 5 seconds. A
///component without a heartbeat for 15 seconds is reported down by GET /health. component
///must equal the envelope source.
class Heartbeat {
    
    ///Publishing component.
    final Component component;
    
    ///Short explanation when not ok, otherwise null.
    final String? detail;
    
    ///Self-reported status.
    final ComponentStatus status;
    
    ///Seconds since the component started.
    final int uptimeS;
    
    ///Component version.
    final String version;

    Heartbeat({
        required this.component,
        required this.detail,
        required this.status,
        required this.uptimeS,
        required this.version,
    });

    Heartbeat copyWith({
        Component? component,
        String? detail,
        ComponentStatus? status,
        int? uptimeS,
        String? version,
    }) => 
        Heartbeat(
            component: component ?? this.component,
            detail: detail ?? this.detail,
            status: status ?? this.status,
            uptimeS: uptimeS ?? this.uptimeS,
            version: version ?? this.version,
        );

    factory Heartbeat.fromJson(Map<String, dynamic> json) => Heartbeat(
        component: componentValues.map[json["component"]]!,
        detail: json["detail"],
        status: componentStatusValues.map[json["status"]]!,
        uptimeS: json["uptime_s"],
        version: json["version"],
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "component": componentValues.reverse[component],
        "detail": detail,
        "status": componentStatusValues.reverse[status],
        "uptime_s": uptimeS,
        "version": version,
    }..removeWhere((_, value) => value == null);
}


///Publishing component.
///
///Process on the Pi that publishes to the event bus. bridge owns the serial link to the
///ESP32, recorder writes history.
enum Component {
    API,
    BRIDGE,
    ENGINE,
    RECORDER,
    VISION,
    VOICE
}

final componentValues = EnumValues({
    "api": Component.API,
    "bridge": Component.BRIDGE,
    "engine": Component.ENGINE,
    "recorder": Component.RECORDER,
    "vision": Component.VISION,
    "voice": Component.VOICE
});


///Wrapper of every event bus message and of every Server-Sent Event on GET /events. The
///topic fixes the payload schema and the only allowed publisher, enforced by the allOf
///rules below and listed in bus.md.
class BusEnvelope {
    
    ///Contract version of the publisher.
    final String contractVersion;
    
    ///Chain id. The request_id for anything caused by a voice or app request, otherwise the
    ///decision id from decision.action onward, null for raw sensor and perception events.
    final String? correlationId;
    
    ///Unique message id. Consumers drop duplicates by id.
    final String id;
    
    ///Topic payload, schema given by the topic.
    final Map<String, dynamic> payload;
    
    ///Publishing component.
    final Component source;
    
    ///Logical topic.
    final BusTopic topic;
    
    ///Publish time on the Pi clock, ISO 8601 UTC.
    final DateTime ts;

    BusEnvelope({
        required this.contractVersion,
        required this.correlationId,
        required this.id,
        required this.payload,
        required this.source,
        required this.topic,
        required this.ts,
    });

    BusEnvelope copyWith({
        String? contractVersion,
        String? correlationId,
        String? id,
        Map<String, dynamic>? payload,
        Component? source,
        BusTopic? topic,
        DateTime? ts,
    }) => 
        BusEnvelope(
            contractVersion: contractVersion ?? this.contractVersion,
            correlationId: correlationId ?? this.correlationId,
            id: id ?? this.id,
            payload: payload ?? this.payload,
            source: source ?? this.source,
            topic: topic ?? this.topic,
            ts: ts ?? this.ts,
        );

    factory BusEnvelope.fromJson(Map<String, dynamic> json) => BusEnvelope(
        contractVersion: json["contract_version"],
        correlationId: json["correlation_id"],
        id: json["id"],
        payload: Map.from(json["payload"]).map((k, v) => MapEntry<String, dynamic>(k, v)),
        source: componentValues.map[json["source"]]!,
        topic: busTopicValues.map[json["topic"]]!,
        ts: DateTime.parse(json["ts"]),
    );

    Map<String, dynamic> toJson() => <String, dynamic>{
        "contract_version": contractVersion,
        "correlation_id": correlationId,
        "id": id,
        "payload": Map.from(payload).map((k, v) => MapEntry<String, dynamic>(k, v)),
        "source": componentValues.reverse[source],
        "topic": busTopicValues.reverse[topic],
        "ts": ts.toIso8601String(),
    }..removeWhere((_, value) => value == null);
}

class EnumValues<T> {
    Map<String, T> map;
    late Map<T, String> reverseMap;

    EnumValues(this.map);

    Map<T, String> get reverse {
            reverseMap = map.map((k, v) => MapEntry(v, k));
            return reverseMap;
    }
}
