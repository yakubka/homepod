
import 'package:flutter/material.dart';
import '../services/vesta_service.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final VestaService service = VestaService.instance;

  late TextEditingController ipController;
  late TextEditingController portController;

  late bool autoReconnect;
  late int refreshSeconds;

  bool saving = false;

  @override
  void initState() {
    super.initState();

    ipController = TextEditingController(text: service.ip);
    portController = TextEditingController(
      text: service.port.toString(),
    );

    autoReconnect = service.autoReconnect;
    refreshSeconds = [3, 5, 10].contains(service.refreshSeconds)
        ? service.refreshSeconds
        : 5;
  }

  @override
  void dispose() {
    ipController.dispose();
    portController.dispose();
    super.dispose();
  }

  bool isValidIp(String ip) {
    final parts = ip.split('.');

    if (parts.length != 4) return false;

    for (final part in parts) {
      final number = int.tryParse(part);

      if (number == null || number < 0 || number > 255) {
        return false;
      }
    }

    return true;
  }

  Future<void> saveSettings() async {
    final ip = ipController.text.trim();
    final port = int.tryParse(portController.text.trim());

    if (!isValidIp(ip) ||
        port == null ||
        port < 1 ||
        port > 65535) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Please enter a valid IP address and port'),
        ),
      );
      return;
    }

    setState(() => saving = true);

    try {
      await service.saveSettings(
        newIp: ip,
        newPort: port,
        newRefresh: refreshSeconds,
        newAutoReconnect: autoReconnect,
      );

      if (!mounted) return;

      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Settings saved successfully'),
        ),
      );
    } catch (e) {
      if (!mounted) return;

      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Failed to save settings: $e')),
      );
    } finally {
      if (mounted) {
        setState(() => saving = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Settings'),
        centerTitle: true,
      ),

      body: AnimatedBuilder(
        animation: service,
        builder: (context, child) {
          return ListView(
            padding: const EdgeInsets.all(20),
            children: [

              // DEVICE CONNECTION
              const Text(
                'Device Connection',
                style: TextStyle(
                  fontSize: 20,
                  fontWeight: FontWeight.bold,
                ),
              ),

              const SizedBox(height: 8),

              const Text(
                'Configure your VESTA classroom device.',
                style: TextStyle(color: Colors.grey),
              ),

              const SizedBox(height: 24),

              // RASPBERRY PI IP
              TextField(
                controller: ipController,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(
                  labelText: 'Raspberry Pi IP Address',
                  hintText: '192.168.1.50',
                  prefixIcon: Icon(Icons.wifi),
                  border: OutlineInputBorder(),
                ),
              ),

              const SizedBox(height: 16),

              // API PORT
              TextField(
                controller: portController,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(
                  labelText: 'API Port',
                  hintText: '8000',
                  prefixIcon: Icon(Icons.settings_ethernet),
                  border: OutlineInputBorder(),
                ),
              ),

              const SizedBox(height: 24),
              const Divider(),

              // AUTO RECONNECT
              SwitchListTile(
                title: const Text('Auto Reconnect'),
                subtitle: const Text(
                  'Automatically retry when connection is lost',
                ),
                secondary: const Icon(Icons.sync),
                value: autoReconnect,
                onChanged: (value) {
                  setState(() {
                    autoReconnect = value;
                  });
                },
              ),

              const SizedBox(height: 16),

              // SENSOR REFRESH INTERVAL
              const Text(
                'Sensor Refresh Interval',
                style: TextStyle(
                  fontSize: 16,
                  fontWeight: FontWeight.w600,
                ),
              ),

              const SizedBox(height: 8),

              DropdownButtonFormField<int>(
                initialValue: refreshSeconds,
                decoration: const InputDecoration(
                  border: OutlineInputBorder(),
                  prefixIcon: Icon(Icons.timer),
                ),
                items: const [
                  DropdownMenuItem(
                    value: 3,
                    child: Text('Every 3 seconds'),
                  ),
                  DropdownMenuItem(
                    value: 5,
                    child: Text('Every 5 seconds'),
                  ),
                  DropdownMenuItem(
                    value: 10,
                    child: Text('Every 10 seconds'),
                  ),
                ],
                onChanged: (value) {
                  if (value != null) {
                    setState(() {
                      refreshSeconds = value;
                    });
                  }
                },
              ),

              const SizedBox(height: 24),

              // SAVE BUTTON
              SizedBox(
                width: double.infinity,
                child: ElevatedButton.icon(
                  onPressed: saving ? null : saveSettings,
                  icon: const Icon(Icons.save),
                  label: Text(
                    saving ? 'Saving...' : 'Save Settings',
                  ),
                ),
              ),

              const SizedBox(height: 12),

              // TEST CONNECTION
              SizedBox(
                width: double.infinity,
                child: OutlinedButton.icon(
                  onPressed: service.checking
                      ? null
                      : () async {
                          await service.checkConnection();
                        },
                  icon: const Icon(Icons.wifi_find),
                  label: Text(
                    service.checking
                        ? 'Connecting...'
                        : 'Test Connection',
                  ),
                ),
              ),

              const SizedBox(height: 24),

              // CONNECTION STATUS
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text(
                        'Connection Status',
                        style: TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.bold,
                        ),
                      ),

                      const SizedBox(height: 12),

                      Row(
                        children: [
                          Icon(
                            service.connected
                                ? Icons.check_circle
                                : Icons.cancel,
                            color: service.connected
                                ? Colors.green
                                : Colors.red,
                          ),

                          const SizedBox(width: 10),

                          Text(
                            service.checking
                                ? 'Connecting...'
                                : service.ip.isEmpty
                                    ? 'Not Configured'
                                    : service.connected
                                        ? 'Connected'
                                        : 'Disconnected',
                          ),
                        ],
                      ),

                      if (service.error != null) ...[
                        const SizedBox(height: 12),
                        Text(
                          service.error!,
                          style: const TextStyle(
                            color: Colors.red,
                            fontSize: 12,
                          ),
                        ),
                      ],

                      if (service.lastUpdated != null) ...[
                        const SizedBox(height: 12),
                        Text(
                          'Last updated: ${service.lastUpdated}',
                          style: const TextStyle(
                            fontSize: 12,
                            color: Colors.grey,
                          ),
                        ),
                      ],
                    ],
                  ),
                ),
              ),

              const SizedBox(height: 32),

              // ABOUT VESTA
              const Center(
                child: Column(
                  children: [
                    Text(
                      'VESTA Classroom System',
                      style: TextStyle(
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    SizedBox(height: 4),
                    Text(
                      'Version 1.0.0',
                      style: TextStyle(color: Colors.grey),
                    ),
                  ],
                ),
              ),

              const SizedBox(height: 24),
            ],
          );
        },
      ),
    );
  }
}
