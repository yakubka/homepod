import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

class VestaService extends ChangeNotifier {
  VestaService._();

  static final VestaService instance = VestaService._();

  final _prefs = SharedPreferencesAsync();

  String ip = '';
  int port = 8000;
  int refreshSeconds = 5;
  bool autoReconnect = true;

  bool connected = false;
  bool checking = false;
  String? error;

  Map<String, dynamic>? sensorData;
  DateTime? lastUpdated;

  Timer? _timer;

  Future<void> initialize() async {
    ip = await _prefs.getString('vesta_ip') ?? '';
    port = await _prefs.getInt('vesta_port') ?? 8000;
    refreshSeconds = await _prefs.getInt('vesta_refresh') ?? 5;
    autoReconnect = await _prefs.getBool('vesta_reconnect') ?? true;

    notifyListeners();
    _startPolling();

    if (ip.isNotEmpty) {
      unawaited(checkConnection());
    }
  }

  Future<void> saveSettings({
    required String newIp,
    required int newPort,
    required int newRefresh,
    required bool newAutoReconnect,
  }) async {
    _timer?.cancel();

    ip = newIp.trim();
    port = newPort;
    refreshSeconds = newRefresh;
    autoReconnect = newAutoReconnect;

    await _prefs.setString('vesta_ip', ip);
    await _prefs.setInt('vesta_port', port);
    await _prefs.setInt('vesta_refresh', refreshSeconds);
    await _prefs.setBool('vesta_reconnect', autoReconnect);

    connected = false;
    sensorData = null;
    lastUpdated = null;
    error = null;

    notifyListeners();
    _startPolling();

    if (ip.isNotEmpty) {
      await checkConnection();
    }
  }

  Future<void> checkConnection() async {
    if (checking) return;

    if (ip.isEmpty) {
      connected = false;
      error = 'Raspberry Pi IP is not configured';
      notifyListeners();
      return;
    }

    checking = true;
    notifyListeners();

    try {
      final url = Uri(
        scheme: 'http',
        host: ip,
        port: port,
        path: '/api/sensors',
      );

      final response = await http.get(url).timeout(
        const Duration(seconds: 5),
      );

      if (response.statusCode != 200) {
        throw Exception('Sensor API HTTP ${response.statusCode}');
      }

      final data = jsonDecode(response.body);

      if (data is! Map<String, dynamic>) {
        throw const FormatException('Invalid sensor JSON');
      }

      sensorData = data;
      connected = true;
      lastUpdated = DateTime.now();
      error = null;
    } catch (e) {
      connected = false;
      error = e.toString();
    } finally {
      checking = false;
      notifyListeners();
    }
  }

  // Lighting uses a separate endpoint from the sensor GET endpoint.
  // A successful response means the API acknowledged the command;
  // it does NOT by itself prove that the physical light changed.
  Future<void> _postLighting(Map<String, dynamic> payload) async {
    if (ip.isEmpty) {
      throw StateError('Configure the Raspberry Pi IP in Settings first');
    }

    final url = Uri(
      scheme: 'http',
      host: ip,
      port: port,
      path: '/api/lighting',
    );

    final response = await http
        .post(
          url,
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode(payload),
        )
        .timeout(const Duration(seconds: 5));

    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw Exception(
        'Lighting API HTTP ${response.statusCode}: ${response.body}',
      );
    }

    if (response.statusCode == 204) return;

    // API contract: reply with {"success": true} when accepted.
    final result = jsonDecode(response.body);
    if (result is! Map<String, dynamic> || result['success'] != true) {
      throw const FormatException(
        'Lighting API must respond with {"success": true}',
      );
    }
  }

  Future<void> setLightingMode(bool automatic) {
    return _postLighting({
      'action': 'set_mode',
      'automatic': automatic,
    });
  }

  Future<void> setSmartBulb(bool on) {
    return _postLighting({
      'action': 'set_bulb',
      'on': on,
    });
  }

  Future<void> setZoneBrightness(String zone, int brightness) {
    if (!['left', 'center', 'right'].contains(zone)) {
      throw ArgumentError.value(zone, 'zone', 'Unknown lighting zone');
    }
    if (brightness < 0 || brightness > 100) {
      throw RangeError.range(brightness, 0, 100, 'brightness');
    }
    return _postLighting({
      'action': 'set_brightness',
      'zone': zone,
      'brightness': brightness,
    });
  }

  void _startPolling() {
    _timer?.cancel();
    _timer = Timer.periodic(
      Duration(seconds: refreshSeconds),
      (_) {
        if (ip.isEmpty) return;
        if (connected || autoReconnect) {
          unawaited(checkConnection());
        }
      },
    );
  }
}
