import 'package:flutter/material.dart';

import '../main.dart';
import '../widgets/common.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  Map<String, dynamic>? provider;
  bool loaded = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (!loaded) {
      loaded = true;
      _loadProvider();
    }
  }

  Future<void> _loadProvider() async {
    try {
      final value = await AppScope.of(context).api.providerSettings();
      if (mounted) setState(() => provider = value);
    } on Object {
      // The global offline state already explains connection failures.
    }
  }

  Future<void> _editEndpoint() async {
    final state = AppScope.of(context);
    final controller = TextEditingController(text: state.endpoint);
    final accepted = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('KnowledgeDebt 后端'),
        content: TextField(
          controller: controller,
          keyboardType: TextInputType.url,
          decoration: const InputDecoration(labelText: 'API 地址'),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('保存并测试'),
          ),
        ],
      ),
    );
    if (accepted == true && mounted) {
      await state.setEndpoint(controller.text);
      await _loadProvider();
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = AppScope.of(context);
    final configured = provider?['configured'] == true;
    return ListView(
      children: [
        const PageHeading('设置', subtitle: 'Provider、隐私与连接'),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: Column(
            children: [
              Card(
                child: Column(
                  children: [
                    ListTile(
                      leading: const Icon(Icons.dns_outlined),
                      title: const Text('后端地址'),
                      subtitle: Text(state.endpoint),
                      trailing: const Icon(Icons.edit_outlined),
                      onTap: _editEndpoint,
                    ),
                    const Divider(height: 1),
                    ListTile(
                      leading: Icon(
                        configured
                            ? Icons.check_circle_outline
                            : Icons.warning_amber_rounded,
                      ),
                      title: const Text('AI / ASR Provider'),
                      subtitle: Text(
                        configured
                            ? '${provider?['ai_model']} · ${provider?['asr_model']}'
                            : '尚未配置 API Key',
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 12),
              const Card(
                child: Padding(
                  padding: EdgeInsets.all(20),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '隐私边界',
                        style: TextStyle(
                          fontWeight: FontWeight.w800,
                          fontSize: 17,
                        ),
                      ),
                      SizedBox(height: 10),
                      Text(
                        '• 课程元数据与录音优先保存在本机 / 自托管后端。\n• 每次发送给 AI 或 ASR 前都会明确确认。\n• API Key 只存在后端环境变量，不进入客户端。',
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 12),
              const Card(
                child: ListTile(
                  leading: Icon(Icons.info_outline),
                  title: Text('KnowledgeDebt 0.1.0'),
                  subtitle: Text('Vibe Coding 开源实验项目 · Owner HanzhiOvO'),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}
