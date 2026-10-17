import 'package:flutter/material.dart';
import 'screens/settings_screen.dart';
import 'services/vesta_service.dart';


Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  await VestaService.instance.initialize();


  runApp(const HomePodApp());
}

class HomePodApp extends StatelessWidget {
  const HomePodApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'HomePod',
      theme: ThemeData(
        brightness: Brightness.dark,
        scaffoldBackgroundColor: const Color(0xFF071329),
        useMaterial3: true,
      ),
      home: const MainScreen(),
    );
  }
}
class MainScreen extends StatefulWidget {
  const MainScreen({super.key});

  @override
  State<MainScreen> createState() => _MainScreenState();
}

class _MainScreenState extends State<MainScreen> {
  int currentIndex = 0;

  final screens = const [
    HomeScreen(),
    DeviceControlScreen(),
    MusicScreen(),
    SensorScreen(),
    SettingsScreen(),
  ];

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: screens[currentIndex],

      bottomNavigationBar: BottomNavigationBar(
        currentIndex: currentIndex,
        type: BottomNavigationBarType.fixed,
        backgroundColor: const Color(0xFF101D35),
        selectedItemColor: const Color(0xFF4CC9FF),
        unselectedItemColor: Colors.white54,

        onTap: (index) {
          setState(() {
            currentIndex = index;
          });
        },

        items: const [
          BottomNavigationBarItem(
            icon: Icon(Icons.home),
            label: 'Home',
          ),
          BottomNavigationBarItem(
            icon: Icon(Icons.tune),
            label: 'Controls',
          ),
          BottomNavigationBarItem(
            icon: Icon(Icons.music_note),
            label: 'Music',
          ),
          BottomNavigationBarItem(
            icon: Icon(Icons.sensors),
            label: 'Sensors',
          ),
          BottomNavigationBarItem(
            icon: Icon(Icons.settings),
            label: 'Settings',
          ),
        ],
      ),
    );
  }
}

class HomeScreen extends StatelessWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context) {
    const Color backgroundColor = Color(0xFF08111F);
    const Color cardColor = Color(0xFF111D2E);
    const Color blueColor = Color(0xFF4DA3FF);

    return Scaffold(
      backgroundColor: backgroundColor,
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Header
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  const Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'CLASS POD',
                        style: TextStyle(
                          color: Colors.white,
                          fontSize: 28,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                      SizedBox(height: 4),
                      Text(
                        'Smart Lecture Room',
                        style: TextStyle(
                          color: Colors.white60,
                          fontSize: 14,
                        ),
                      ),
                    ],
                  ),

                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 12,
                      vertical: 7,
                    ),
                    decoration: BoxDecoration(
                      color: Colors.green.withOpacity(0.15),
                      borderRadius: BorderRadius.circular(20),
                    ),
                    child: const Row(
                      children: [
                        Icon(
                          Icons.circle,
                          color: Colors.greenAccent,
                          size: 9,
                        ),
                        SizedBox(width: 7),
                        Text(
                          'Connected',
                          style: TextStyle(
                            color: Colors.greenAccent,
                            fontSize: 13,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),

              const SizedBox(height: 28),

              const Text(
                'Classroom Overview',
                style: TextStyle(
                  color: Colors.white,
                  fontSize: 19,
                  fontWeight: FontWeight.bold,
                ),
              ),

              const SizedBox(height: 14),

              // Overview Cards
              Row(
                children: [
                  Expanded(
                    child: _buildInfoCard(
                      icon: Icons.people_alt_rounded,
                      title: 'Students',
                      value: '23',
                      unit: 'Detected',
                      iconColor: blueColor,
                      cardColor: cardColor,
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: _buildInfoCard(
                      icon: Icons.thermostat_rounded,
                      title: 'Temperature',
                      value: '24°C',
                      unit: 'Room',
                      iconColor: Colors.orangeAccent,
                      cardColor: cardColor,
                    ),
                  ),
                ],
              ),

              const SizedBox(height: 12),

              Row(
                children: [
                  Expanded(
                    child: _buildInfoCard(
                      icon: Icons.water_drop_rounded,
                      title: 'Humidity',
                      value: '52%',
                      unit: 'Normal',
                      iconColor: Colors.lightBlueAccent,
                      cardColor: cardColor,
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: _buildInfoCard(
                      icon: Icons.light_mode_rounded,
                      title: 'Light Level',
                      value: '420',
                      unit: 'lux',
                      iconColor: Colors.amberAccent,
                      cardColor: cardColor,
                    ),
                  ),
                ],
              ),

              const SizedBox(height: 28),

              // Occupancy
              const Text(
                'Classroom Occupancy',
                style: TextStyle(
                  color: Colors.white,
                  fontSize: 19,
                  fontWeight: FontWeight.bold,
                ),
              ),

              const SizedBox(height: 5),

              const Text(
                'Student distribution by zone',
                style: TextStyle(
                  color: Colors.white54,
                  fontSize: 13,
                ),
              ),

              const SizedBox(height: 14),

              Row(
                children: [
                  Expanded(
                    child: _buildZoneCard(
                      zone: 'LEFT',
                      count: '7',
                      color: Colors.blueAccent,
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: _buildZoneCard(
                      zone: 'CENTER',
                      count: '10',
                      color: Colors.greenAccent,
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: _buildZoneCard(
                      zone: 'RIGHT',
                      count: '6',
                      color: Colors.redAccent,
                    ),
                  ),
                ],
              ),

              const SizedBox(height: 28),

              // System Status
              const Text(
                'System Status',
                style: TextStyle(
                  color: Colors.white,
                  fontSize: 19,
                  fontWeight: FontWeight.bold,
                ),
              ),

              const SizedBox(height: 14),

              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(18),
                decoration: BoxDecoration(
                  color: cardColor,
                  borderRadius: BorderRadius.circular(18),
                ),
                child: const Column(
                  children: [
                    _SystemStatusRow(
                      icon: Icons.memory_rounded,
                      name: 'Raspberry Pi',
                      status: 'Online',
                    ),
                    Divider(
                      color: Colors.white10,
                      height: 25,
                    ),
                    _SystemStatusRow(
                      icon: Icons.sensors_rounded,
                      name: 'Sensors',
                      status: 'Online',
                    ),
                    Divider(
                      color: Colors.white10,
                      height: 25,
                    ),
                    _SystemStatusRow(
                      icon: Icons.wifi_rounded,
                      name: 'Network',
                      status: 'Connected',
                    ),
                  ],
                ),
              ),

              const SizedBox(height: 20),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildInfoCard({
    required IconData icon,
    required String title,
    required String value,
    required String unit,
    required Color iconColor,
    required Color cardColor,
  }) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: cardColor,
        borderRadius: BorderRadius.circular(18),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(
            icon,
            color: iconColor,
            size: 25,
          ),
          const SizedBox(height: 14),
          Text(
            title,
            style: const TextStyle(
              color: Colors.white54,
              fontSize: 12,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            value,
            style: const TextStyle(
              color: Colors.white,
              fontSize: 23,
              fontWeight: FontWeight.bold,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            unit,
            style: const TextStyle(
              color: Colors.white38,
              fontSize: 11,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildZoneCard({
    required String zone,
    required String count,
    required Color color,
  }) {
    return Container(
      padding: const EdgeInsets.symmetric(
        vertical: 18,
        horizontal: 8,
      ),
      decoration: BoxDecoration(
        color: const Color(0xFF111D2E),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: color.withOpacity(0.35),
        ),
      ),
      child: Column(
        children: [
          Container(
            width: 10,
            height: 10,
            decoration: BoxDecoration(
              color: color,
              shape: BoxShape.circle,
            ),
          ),
          const SizedBox(height: 9),
          Text(
            zone,
            style: const TextStyle(
              color: Colors.white54,
              fontSize: 11,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 5),
          Text(
            count,
            style: const TextStyle(
              color: Colors.white,
              fontSize: 24,
              fontWeight: FontWeight.bold,
            ),
          ),
          const Text(
            'Students',
            style: TextStyle(
              color: Colors.white38,
              fontSize: 10,
            ),
          ),
        ],
      ),
    );
  }
}

class _SystemStatusRow extends StatelessWidget {
  final IconData icon;
  final String name;
  final String status;

  const _SystemStatusRow({
    required this.icon,
    required this.name,
    required this.status,
  });

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Container(
          padding: const EdgeInsets.all(9),
          decoration: BoxDecoration(
            color: const Color(0xFF4DA3FF).withOpacity(0.12),
            borderRadius: BorderRadius.circular(10),
          ),
          child: Icon(
            icon,
            color: const Color(0xFF4DA3FF),
            size: 20,
          ),
        ),
        const SizedBox(width: 13),
        Expanded(
          child: Text(
            name,
            style: const TextStyle(
              color: Colors.white,
              fontSize: 14,
              fontWeight: FontWeight.w500,
            ),
          ),
        ),
        const Icon(
          Icons.circle,
          color: Colors.greenAccent,
          size: 8,
        ),
        const SizedBox(width: 6),
        Text(
          status,
          style: const TextStyle(
            color: Colors.greenAccent,
            fontSize: 12,
          ),
        ),
      ],
    );
  }
}
class DeviceControlScreen extends StatefulWidget {
  const DeviceControlScreen({super.key});

  @override
  State<DeviceControlScreen> createState() =>
      _DeviceControlScreenState();
}

class _DeviceControlScreenState extends State<DeviceControlScreen> {
  bool automaticMode = true;
  bool smartBulb = true;

  double leftBrightness = 70;
  double centerBrightness = 80;
  double rightBrightness = 65;

  // These are initial UI values, NOT verified hardware readings.
  // Confirmed values are retained for rollback if POST fails.
  double _confirmedLeftBrightness = 70;
  double _confirmedCenterBrightness = 80;
  double _confirmedRightBrightness = 65;

  bool _lightingBusy = false;
  String? _lightingFeedback;
  bool _lightingError = false;

  Future<void> _sendLighting({
    required Future<void> Function() request,
    required VoidCallback onSuccess,
    VoidCallback? onFailure,
    required String successMessage,
  }) async {
    if (_lightingBusy) return;

    setState(() {
      _lightingBusy = true;
      _lightingFeedback = 'Sending lighting command...';
      _lightingError = false;
    });

    try {
      await request();
      if (!mounted) return;
      setState(() {
        onSuccess();
        _lightingFeedback = successMessage;
        _lightingError = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        onFailure?.call();
        _lightingFeedback = 'Command failed: $e';
        _lightingError = true;
      });
    } finally {
      if (mounted) {
        setState(() => _lightingBusy = false);
      }
    }
  }

  Future<void> _sendZoneBrightness(String zone, double value) async {
    await _sendLighting(
      request: () => VestaService.instance.setZoneBrightness(
        zone,
        value.round(),
      ),
      onSuccess: () {
        switch (zone) {
          case 'left':
            _confirmedLeftBrightness = value;
            break;
          case 'center':
            _confirmedCenterBrightness = value;
            break;
          case 'right':
            _confirmedRightBrightness = value;
            break;
        }
      },
      onFailure: () {
        switch (zone) {
          case 'left':
            leftBrightness = _confirmedLeftBrightness;
            break;
          case 'center':
            centerBrightness = _confirmedCenterBrightness;
            break;
          case 'right':
            rightBrightness = _confirmedRightBrightness;
            break;
        }
      },
      successMessage: 'Brightness command sent: $zone = ${value.round()}%',
    );
  }

  String selectedAnnouncement = 'Class will begin shortly';

  final List<String> announcements = [
    'Class will begin shortly',
    'Please take your seats',
    'Please keep the classroom quiet',
    'Class has ended',
  ];

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Classroom Controls',
              style: TextStyle(
                fontSize: 27,
                fontWeight: FontWeight.bold,
              ),
            ),

            const SizedBox(height: 5),

            const Text(
              'Manual and automatic classroom control',
              style: TextStyle(
                color: Colors.white54,
                fontSize: 13,
              ),
            ),

            const SizedBox(height: 16),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: const Color(0xFF111D2E),
                borderRadius: BorderRadius.circular(12),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'Lighting API: POST /api/lighting',
                    style: TextStyle(fontSize: 12, color: Colors.white70),
                  ),
                  const SizedBox(height: 7),
                  Text(
                    _lightingFeedback ??
                        (VestaService.instance.ip.isEmpty
                            ? 'Configure Raspberry Pi IP in Settings first.'
                            : 'Ready to send lighting commands. ' 
                              'Initial slider values are not live hardware readings.'),
                    style: TextStyle(
                      fontSize: 12,
                      color: _lightingError
                          ? Colors.redAccent
                          : Colors.white70,
                    ),
                  ),
                  if (_lightingBusy) ...[
                    const SizedBox(height: 10),
                    const LinearProgressIndicator(),
                  ],
                ],
              ),
            ),

            const SizedBox(height: 25),

            // LIGHTING MODE
            _sectionTitle('Lighting Mode'),

            const SizedBox(height: 12),

            Container(
              padding: const EdgeInsets.all(16),
              decoration: _cardDecoration(),
              child: SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text(
                  'Automatic Lighting',
                  style: TextStyle(
                    fontWeight: FontWeight.bold,
                  ),
                ),
                subtitle: Text(
                  automaticMode
                      ? 'VESTA controls lighting automatically'
                      : 'Manual lighting control enabled',
                  style: const TextStyle(
                    color: Colors.white54,
                  ),
                ),
                value: automaticMode,
                onChanged: _lightingBusy
                    ? null
                    : (value) => _sendLighting(
                          request: () => VestaService.instance
                              .setLightingMode(value),
                          onSuccess: () => automaticMode = value,
                          successMessage: value
                              ? 'Automatic lighting command accepted.'
                              : 'Manual lighting command accepted.',
                        ),
              ),
            ),

            const SizedBox(height: 20),

            // SMART BULB
            _sectionTitle('Smart Bulb'),

            const SizedBox(height: 12),

            Container(
              padding: const EdgeInsets.all(16),
              decoration: _cardDecoration(),
              child: Row(
                children: [
                  const Icon(
                    Icons.lightbulb_outline,
                    color: Color(0xFF4CC9FF),
                    size: 28,
                  ),
                  const SizedBox(width: 14),
                  const Expanded(
                    child: Text(
                      'Main Classroom Light',
                      style: TextStyle(
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ),
                  Switch(
                    value: smartBulb,
                    onChanged: (_lightingBusy || automaticMode)
                        ? null
                        : (value) => _sendLighting(
                              request: () => VestaService.instance
                                  .setSmartBulb(value),
                              onSuccess: () => smartBulb = value,
                              successMessage: value
                                  ? 'Light ON command accepted.'
                                  : 'Light OFF command accepted.',
                            ),
                  ),
                ],
              ),
            ),

            const SizedBox(height: 25),

            // ZONE LIGHTING
            _sectionTitle('Zone Brightness'),

            const SizedBox(height: 12),

            _brightnessControl(
              'Left Zone',
              leftBrightness,
              (value) {
                setState(() {
                  leftBrightness = value;
                });
              },
              (value) => _sendZoneBrightness('left', value),
            ),

            _brightnessControl(
              'Center Zone',
              centerBrightness,
              (value) {
                setState(() {
                  centerBrightness = value;
                });
              },
              (value) => _sendZoneBrightness('center', value),
            ),

            _brightnessControl(
              'Right Zone',
              rightBrightness,
              (value) {
                setState(() {
                  rightBrightness = value;
                });
              },
              (value) => _sendZoneBrightness('right', value),
            ),

            const SizedBox(height: 25),

            // ANNOUNCEMENTS
            _sectionTitle('Announcements'),

            const SizedBox(height: 12),

            Container(
              padding: const EdgeInsets.all(16),
              decoration: _cardDecoration(),
              child: Column(
                children: [
                  DropdownButtonFormField<String>(
                    value: selectedAnnouncement,
                    decoration: const InputDecoration(
                      labelText: 'Predefined Message',
                      border: OutlineInputBorder(),
                    ),
                    items: announcements.map((message) {
                      return DropdownMenuItem(
                        value: message,
                        child: Text(message),
                      );
                    }).toList(),
                    onChanged: (value) {
                      if (value != null) {
                        setState(() {
                          selectedAnnouncement = value;
                        });
                      }
                    },
                  ),

                  const SizedBox(height: 15),

                  SizedBox(
                    width: double.infinity,
                    child: ElevatedButton.icon(
                      icon: const Icon(Icons.campaign_outlined),
                      label: const Text('Send Announcement'),
                      onPressed: () {
                        ScaffoldMessenger.of(context).showSnackBar(
                          SnackBar(
                            content: Text(
                              'Mock announcement: $selectedAnnouncement',
                            ),
                          ),
                        );
                      },
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _sectionTitle(String title) {
    return Text(
      title,
      style: const TextStyle(
        fontSize: 18,
        fontWeight: FontWeight.bold,
      ),
    );
  }

  Widget _brightnessControl(
    String title,
    double value,
    ValueChanged<double> onChanged,
    ValueChanged<double> onChangeEnd,
  ) {
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(16),
      decoration: _cardDecoration(),
      child: Column(
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  title,
                  style: const TextStyle(
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
              Text(
                '${value.round()}%',
                style: const TextStyle(
                  color: Color(0xFF4CC9FF),
                ),
              ),
            ],
          ),

          Slider(
            value: value,
            min: 0,
            max: 100,
            onChanged: (automaticMode || _lightingBusy)
                ? null
                : onChanged,
            onChangeEnd: (automaticMode || _lightingBusy)
                ? null
                : onChangeEnd,
          ),
        ],
      ),
    );
  }

  BoxDecoration _cardDecoration() {
    return BoxDecoration(
      color: const Color(0xFF111D2E),
      borderRadius: BorderRadius.circular(18),
    );
  }
}

class MusicScreen extends StatefulWidget {
  const MusicScreen({super.key});

  @override
  State<MusicScreen> createState() => _MusicScreenState();
}

class _MusicScreenState extends State<MusicScreen> {
  bool isPlaying = false;

  double volume = 75;
  double progress = 35;

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(20),
        child: Column(
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                const Text(
                  'Music Control',
                  style: TextStyle(
                    fontSize: 27,
                    fontWeight: FontWeight.bold,
                  ),
                ),

                Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 12,
                    vertical: 6,
                  ),
                  decoration: BoxDecoration(
                    color:
                        const Color(0xFF4CC9FF).withOpacity(0.12),
                    borderRadius: BorderRadius.circular(20),
                  ),
                  child: const Text(
                    'Mock Player',
                    style: TextStyle(
                      color: Color(0xFF4CC9FF),
                      fontSize: 12,
                    ),
                  ),
                ),
              ],
            ),

            const SizedBox(height: 35),

            Container(
              width: 250,
              height: 250,
              decoration: BoxDecoration(
                color: const Color(0xFF111D2E),
                borderRadius: BorderRadius.circular(25),
              ),
              child: const Icon(
                Icons.music_note,
                size: 90,
                color: Color(0xFF4CC9FF),
              ),
            ),

            const SizedBox(height: 25),

            const Text(
              'Classroom Audio',
              style: TextStyle(
                fontSize: 21,
                fontWeight: FontWeight.bold,
              ),
            ),

            const SizedBox(height: 5),

            const Text(
              'VESTA Speaker',
              style: TextStyle(
                color: Colors.white54,
              ),
            ),

            const SizedBox(height: 25),

            Slider(
              value: progress,
              min: 0,
              max: 100,
              onChanged: (value) {
                setState(() {
                  progress = value;
                });
              },
            ),

            const SizedBox(height: 20),

            Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                IconButton(
                  iconSize: 42,
                  onPressed: () {},
                  icon: const Icon(
                    Icons.skip_previous_rounded,
                  ),
                ),

                const SizedBox(width: 20),

                CircleAvatar(
                  radius: 34,
                  backgroundColor:
                      const Color(0xFF4CC9FF),
                  child: IconButton(
                    iconSize: 38,
                    color: const Color(0xFF071329),
                    icon: Icon(
                      isPlaying
                          ? Icons.pause_rounded
                          : Icons.play_arrow_rounded,
                    ),
                    onPressed: () {
                      setState(() {
                        isPlaying = !isPlaying;
                      });
                    },
                  ),
                ),

                const SizedBox(width: 20),

                IconButton(
                  iconSize: 42,
                  onPressed: () {},
                  icon: const Icon(
                    Icons.skip_next_rounded,
                  ),
                ),
              ],
            ),

            const SizedBox(height: 30),

            Container(
              padding: const EdgeInsets.all(18),
              decoration: BoxDecoration(
                color: const Color(0xFF111D2E),
                borderRadius: BorderRadius.circular(18),
              ),
              child: Column(
                children: [
                  Row(
                    children: [
                      const Icon(
                        Icons.volume_up_outlined,
                        color: Color(0xFF4CC9FF),
                      ),

                      const SizedBox(width: 10),

                      const Text(
                        'Volume',
                        style: TextStyle(
                          fontWeight: FontWeight.bold,
                        ),
                      ),

                      const Spacer(),

                      Text(
                        '${volume.round()}%',
                        style: const TextStyle(
                          color: Color(0xFF4CC9FF),
                        ),
                      ),
                    ],
                  ),

                  Slider(
                    value: volume,
                    min: 0,
                    max: 100,
                    onChanged: (value) {
                      setState(() {
                        volume = value;
                      });
                    },
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class SensorScreen extends StatelessWidget {
  const SensorScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Row(
              mainAxisAlignment:
                  MainAxisAlignment.spaceBetween,
              children: [
                Column(
                  crossAxisAlignment:
                      CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Live Sensors',
                      style: TextStyle(
                        fontSize: 27,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    SizedBox(height: 4),
                    Text(
                      'Classroom environment monitoring',
                      style: TextStyle(
                        color: Colors.white54,
                      ),
                    ),
                  ],
                ),

                Chip(
                  label: Text('Mock Data'),
                ),
              ],
            ),

            const SizedBox(height: 25),

            Row(
              children: [
                Expanded(
                  child: _sensorCard(
                    Icons.thermostat,
                    'Temperature',
                    '24°C',
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: _sensorCard(
                    Icons.water_drop,
                    'Humidity',
                    '52%',
                  ),
                ),
              ],
            ),

            const SizedBox(height: 12),

            Row(
              children: [
                Expanded(
                  child: _sensorCard(
                    Icons.light_mode,
                    'Light Level',
                    '420 lux',
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: _sensorCard(
                    Icons.people,
                    'Students',
                    '23',
                  ),
                ),
              ],
            ),

            const SizedBox(height: 30),

            const Text(
              'Occupancy Zones',
              style: TextStyle(
                fontSize: 19,
                fontWeight: FontWeight.bold,
              ),
            ),

            const SizedBox(height: 15),

            Row(
              children: [
                Expanded(
                  child: _zoneCard(
                    'LEFT',
                    '7',
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: _zoneCard(
                    'CENTER',
                    '10',
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: _zoneCard(
                    'RIGHT',
                    '6',
                  ),
                ),
              ],
            ),

            const SizedBox(height: 30),

            Container(
              padding: const EdgeInsets.all(18),
              decoration: BoxDecoration(
                color: const Color(0xFF111D2E),
                borderRadius: BorderRadius.circular(18),
              ),
              child: const Row(
                children: [
                  Icon(
                    Icons.info_outline,
                    color: Color(0xFF4CC9FF),
                  ),
                  SizedBox(width: 12),
                  Expanded(
                    child: Text(
                      'Hellooooooo guyss i needd android phoneee huhuhuhuhuhuhuhuhuhuhuhuh '
                      'Live values will be received from the Raspberry Pi through the REST API.',
                      style: TextStyle(
                        color: Colors.white70,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _sensorCard(
    IconData icon,
    String title,
    String value,
  ) {
    return Container(
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: const Color(0xFF111D2E),
        borderRadius: BorderRadius.circular(18),
      ),
      child: Column(
        crossAxisAlignment:
            CrossAxisAlignment.start,
        children: [
          Icon(
            icon,
            color: const Color(0xFF4CC9FF),
          ),
          const SizedBox(height: 15),
          Text(
            title,
            style: const TextStyle(
              color: Colors.white54,
              fontSize: 12,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            value,
            style: const TextStyle(
              fontSize: 22,
              fontWeight: FontWeight.bold,
            ),
          ),
        ],
      ),
    );
  }

  Widget _zoneCard(
    String zone,
    String count,
  ) {
    return Container(
      padding:
          const EdgeInsets.symmetric(
        vertical: 20,
      ),
      decoration: BoxDecoration(
        color: const Color(0xFF111D2E),
        borderRadius: BorderRadius.circular(15),
      ),
      child: Column(
        children: [
          Text(
            zone,
            style: const TextStyle(
              color: Colors.white54,
              fontSize: 11,
            ),
          ),
          const SizedBox(height: 6),
          Text(
            count,
            style: const TextStyle(
              fontSize: 25,
              fontWeight: FontWeight.bold,
            ),
          ),
          const Text(
            'Students',
            style: TextStyle(
              color: Colors.white38,
              fontSize: 10,
            ),
          ),
        ],
      ),
    );
  }
}
