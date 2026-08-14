import 'package:flutter/material.dart';

import '../main.dart';
import '../widgets/common.dart';
import 'session_detail_screen.dart';

class HomeScreen extends StatelessWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final state = AppScope.of(context);
    final home = state.home;
    final sessions = List<Map<String, dynamic>>.from(
      home['sessions'] as List? ?? const [],
    );
    return RefreshIndicator(
      onRefresh: state.refresh,
      child: CustomScrollView(
        physics: const AlwaysScrollableScrollPhysics(),
        slivers: [
          SliverToBoxAdapter(
            child: PageHeading(
              '今天还欠什么？',
              subtitle: state.offline ? '离线模式 · 显示上次同步内容' : '课堂可以缺席，知识不能欠账',
              trailing: state.busy
                  ? const SizedBox(
                      width: 22,
                      height: 22,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : null,
            ),
          ),
          if (sessions.isEmpty)
            const SliverFillRemaining(
              hasScrollBody: false,
              child: EmptyState(
                icon: Icons.auto_stories_outlined,
                title: '还没有课程债务',
                message: '先在“课程”中创建一门课和一次 Session。即使没有录音，Session 也完全有效。',
              ),
            )
          else ...[
            SliverToBoxAdapter(child: _DebtOverview(home: home)),
            SliverPadding(
              padding: const EdgeInsets.fromLTRB(16, 16, 16, 28),
              sliver: SliverList.separated(
                itemCount: sessions.length,
                separatorBuilder: (_, _) => const SizedBox(height: 12),
                itemBuilder: (context, index) =>
                    _SessionCard(session: sessions[index]),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _DebtOverview extends StatelessWidget {
  const _DebtOverview({required this.home});

  final Map<String, dynamic> home;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(horizontal: 16),
    child: Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Row(
          children: [
            Expanded(
              child: _Stat(
                value: '${home['pending_session_count'] ?? 0}',
                label: '待补 Session',
              ),
            ),
            Expanded(
              child: _Stat(
                value: '${home['urgent_debt_count'] ?? 0}',
                label: '紧急',
              ),
            ),
            Expanded(
              child: _Stat(
                value: '${home['minimum_minutes'] ?? 0}m',
                label: '今日最低',
              ),
            ),
          ],
        ),
      ),
    ),
  );
}

class _Stat extends StatelessWidget {
  const _Stat({required this.value, required this.label});
  final String value;
  final String label;

  @override
  Widget build(BuildContext context) => Column(
    children: [
      Text(
        value,
        style: Theme.of(context).textTheme.headlineSmall
            ?.copyWith(fontWeight: FontWeight.w800),
      ),
      Text(label, style: Theme.of(context).textTheme.labelMedium),
    ],
  );
}

class _SessionCard extends StatelessWidget {
  const _SessionCard({required this.session});
  final Map<String, dynamic> session;

  @override
  Widget build(BuildContext context) {
    final debts = session['open_debt_count'] as int? ?? 0;
    return Card(
      child: InkWell(
        borderRadius: BorderRadius.circular(20),
        onTap: () => Navigator.push(
          context,
          MaterialPageRoute<void>(
            builder: (_) =>
                SessionDetailScreen(sessionId: session['id'] as String),
          ),
        ),
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          session['course_name']?.toString() ?? '',
                          style: Theme.of(context).textTheme.labelLarge,
                        ),
                        const SizedBox(height: 4),
                        Text(
                          session['title']?.toString() ?? '',
                          style: Theme.of(context).textTheme.titleLarge
                              ?.copyWith(fontWeight: FontWeight.w800),
                        ),
                      ],
                    ),
                  ),
                  DebtBadge(
                    debts,
                    pending: session['status'] != 'complete' && debts == 0,
                  ),
                ],
              ),
              const SizedBox(height: 20),
              ScoreMeter(
                label: '课堂还原度',
                value: session['reconstruction_score'] as int? ?? 0,
              ),
              const SizedBox(height: 13),
              ScoreMeter(
                label: '学习资料完备度',
                value: session['learning_coverage'] as int? ?? 0,
              ),
              const SizedBox(height: 18),
              Align(
                alignment: Alignment.centerRight,
                child: FilledButton.tonalIcon(
                  onPressed: () => Navigator.push(
                    context,
                    MaterialPageRoute<void>(
                      builder: (_) => SessionDetailScreen(
                        sessionId: session['id'] as String,
                      ),
                    ),
                  ),
                  icon: const Icon(Icons.arrow_forward),
                  label: Text(debts == 0 ? '查看 Session' : '继续补课'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
