import 'package:flutter/material.dart';

import '../main.dart';
import '../widgets/common.dart';
import 'session_detail_screen.dart';

class DebtsScreen extends StatefulWidget {
  const DebtsScreen({super.key});

  @override
  State<DebtsScreen> createState() => _DebtsScreenState();
}

class _DebtsScreenState extends State<DebtsScreen> {
  List<Map<String, dynamic>>? debts;
  bool loaded = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (!loaded) {
      loaded = true;
      debts = const [];
      _load();
    }
  }

  Future<void> _load() async {
    try {
      final result = await AppScope.of(context).api.debts();
      if (mounted) setState(() => debts = result);
    } on Object catch (error) {
      if (mounted) showError(context, error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final open =
        debts?.where((item) => item['status'] != 'mastered').toList() ??
        const [];
    return Column(
      children: [
        PageHeading('知识债务', subtitle: '${open.length} 项待清零'),
        Expanded(
          child: open.isEmpty
              ? const EmptyState(
                  icon: Icons.task_alt,
                  title: '当前没有未偿还债务',
                  message: '新 Session 经课堂分析后，会按目标掌握等级建立知识债务。',
                )
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView.separated(
                    padding: const EdgeInsets.fromLTRB(16, 0, 16, 24),
                    itemCount: open.length,
                    separatorBuilder: (_, _) => const SizedBox(height: 10),
                    itemBuilder: (context, index) {
                      final item = open[index];
                      final current = (item['current_mastery'] as num)
                          .toDouble();
                      final target = item['target_mastery'] as int;
                      return Card(
                        child: InkWell(
                          borderRadius: BorderRadius.circular(20),
                          onTap: () => Navigator.push(
                            context,
                            MaterialPageRoute<void>(
                              builder: (_) => SessionDetailScreen(
                                sessionId: item['session_id'] as String,
                              ),
                            ),
                          ),
                          child: Padding(
                            padding: const EdgeInsets.all(18),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Row(
                                  children: [
                                    Expanded(
                                      child: Text(
                                        item['title'] as String,
                                        style: Theme.of(context)
                                            .textTheme
                                            .titleMedium
                                            ?.copyWith(
                                              fontWeight: FontWeight.w800,
                                            ),
                                      ),
                                    ),
                                    Text('${item['estimated_minutes']} min'),
                                  ],
                                ),
                                const SizedBox(height: 8),
                                Text(
                                  'Mastery ${current.toStringAsFixed(1)} / $target · ${item['status']}',
                                ),
                                const SizedBox(height: 8),
                                LinearProgressIndicator(
                                  value: target == 0 ? 0 : current / target,
                                ),
                              ],
                            ),
                          ),
                        ),
                      );
                    },
                  ),
                ),
        ),
      ],
    );
  }
}
