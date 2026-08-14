import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../main.dart';
import '../widgets/common.dart';
import 'quiz_screen.dart';
import 'recording_screen.dart';

class SessionDetailScreen extends StatefulWidget {
  const SessionDetailScreen({required this.sessionId, super.key});

  final String sessionId;

  @override
  State<SessionDetailScreen> createState() => _SessionDetailScreenState();
}

class _SessionDetailScreenState extends State<SessionDetailScreen> {
  Map<String, dynamic>? session;
  Object? error;
  bool working = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (session == null && error == null) _load();
  }

  Future<void> _load() async {
    try {
      final value = await AppScope.of(context).api.session(widget.sessionId);
      if (mounted) setState(() => session = value);
    } on Object catch (exception) {
      if (mounted) setState(() => error = exception);
    }
  }

  Future<void> _run(Future<void> Function() action) async {
    setState(() => working = true);
    try {
      await action();
      await _load();
      if (mounted) await AppScope.of(context).refresh();
    } on Object catch (exception) {
      if (mounted) showError(context, exception);
    } finally {
      if (mounted) setState(() => working = false);
    }
  }

  Future<void> _record() async {
    final changed = await Navigator.push<bool>(
      context,
      MaterialPageRoute<bool>(
        builder: (_) => RecordingScreen(
          sessionId: widget.sessionId,
          api: AppScope.of(context).api,
        ),
      ),
    );
    if (changed == true) await _load();
  }

  Future<void> _upload() async {
    final result = await FilePicker.pickFile(
      type: FileType.custom,
      allowedExtensions: [
        'pdf',
        'ppt',
        'pptx',
        'txt',
        'md',
        'mp3',
        'm4a',
        'wav',
        'mp4',
      ],
    );
    final path = result?.path;
    if (path == null || !mounted) return;
    final fileNameParts = result!.name.split('.');
    var type = _inferType(fileNameParts.length > 1 ? fileNameParts.last : null);
    var level = type == 'audio' || type == 'video' ? 'classroom' : 'official';
    var coverage = type == 'textbook' || type == 'other' ? .35 : .85;
    var quality = .9;
    var relevance = .9;
    final accepted = await showDialog<bool>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, setDialogState) => AlertDialog(
          title: const Text('资料分类'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              DropdownButtonFormField<String>(
                initialValue: type,
                decoration: const InputDecoration(labelText: '资料类型'),
                items:
                    const {
                          'audio': '课堂录音',
                          'video': '课堂视频',
                          'slides': 'PPT / 课件',
                          'textbook': '教材',
                          'assignment': '作业 / 习题',
                          'syllabus': '教学大纲',
                          'note': '课堂笔记',
                          'other': '其他',
                        }.entries
                        .map(
                          (item) => DropdownMenuItem(
                            value: item.key,
                            child: Text(item.value),
                          ),
                        )
                        .toList(),
                onChanged: (value) => setDialogState(() => type = value!),
              ),
              const SizedBox(height: 12),
              DropdownButtonFormField<String>(
                initialValue: level,
                decoration: const InputDecoration(labelText: '可信等级'),
                items:
                    const {
                          'classroom': 'Level 1 · 真实课堂证据',
                          'official': 'Level 2 · 官方课程资料',
                          'supplementary': 'Level 3 · 外部补充资料',
                        }.entries
                        .map(
                          (item) => DropdownMenuItem(
                            value: item.key,
                            child: Text(item.value),
                          ),
                        )
                        .toList(),
                onChanged: (value) => setDialogState(() => level = value!),
              ),
              const SizedBox(height: 14),
              _QualitySlider(
                label: '本节覆盖率',
                value: coverage,
                onChanged: (value) => setDialogState(() => coverage = value),
              ),
              _QualitySlider(
                label: '资料质量',
                value: quality,
                onChanged: (value) => setDialogState(() => quality = value),
              ),
              _QualitySlider(
                label: '与本节相关度',
                value: relevance,
                onChanged: (value) => setDialogState(() => relevance = value),
              ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('取消'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('添加'),
            ),
          ],
        ),
      ),
    );
    if (accepted == true && mounted) {
      await _run(() async {
        await AppScope.of(context).api.uploadResource(
          sessionId: widget.sessionId,
          filePath: path,
          type: type,
          evidenceLevel: level,
          coverage: coverage,
          quality: quality,
          relevance: relevance,
        );
      });
    }
  }

  String _inferType(String? extension) => switch (extension?.toLowerCase()) {
    'mp3' || 'm4a' || 'wav' => 'audio',
    'mp4' => 'video',
    'ppt' || 'pptx' => 'slides',
    _ => 'other',
  };

  Future<void> _analyze() async {
    if (!await confirmExternalUpload(context, '分析课堂')) return;
    await _run(() async {
      await AppScope.of(context).api.analyze(widget.sessionId);
    });
  }

  Future<void> _quiz() async {
    if (!await confirmExternalUpload(context, '生成并评价验收题')) return;
    if (!mounted) return;
    final changed = await Navigator.push<bool>(
      context,
      MaterialPageRoute<bool>(
        builder: (_) => QuizScreen(
          sessionId: widget.sessionId,
          api: AppScope.of(context).api,
        ),
      ),
    );
    if (changed == true) await _load();
  }

  @override
  Widget build(BuildContext context) {
    final value = session;
    return DefaultTabController(
      length: 4,
      child: Scaffold(
        appBar: AppBar(
          title: Text(value?['title']?.toString() ?? 'Session'),
          bottom: value == null
              ? null
              : const TabBar(
                  isScrollable: true,
                  tabs: [
                    Tab(text: '概览'),
                    Tab(text: '资料'),
                    Tab(text: '课堂还原'),
                    Tab(text: '从零补课'),
                  ],
                ),
        ),
        body: value == null
            ? Center(
                child: error == null
                    ? const CircularProgressIndicator()
                    : Text(error.toString()),
              )
            : Stack(
                children: [
                  TabBarView(
                    children: [
                      _Overview(
                        session: value,
                        onRecord: _record,
                        onUpload: _upload,
                        onAnalyze: _analyze,
                        onQuiz: _quiz,
                      ),
                      _Resources(
                        session: value,
                        onUpload: _upload,
                        onChanged: _load,
                      ),
                      _Reconstruction(session: value, onAnalyze: _analyze),
                      _LearningPath(
                        session: value,
                        onQuiz: _quiz,
                        onChanged: _load,
                      ),
                    ],
                  ),
                  if (working)
                    const Positioned(
                      top: 0,
                      left: 0,
                      right: 0,
                      child: LinearProgressIndicator(),
                    ),
                ],
              ),
      ),
    );
  }
}

class _QualitySlider extends StatelessWidget {
  const _QualitySlider({
    required this.label,
    required this.value,
    required this.onChanged,
  });
  final String label;
  final double value;
  final ValueChanged<double> onChanged;

  @override
  Widget build(BuildContext context) => Row(
    children: [
      SizedBox(width: 92, child: Text(label)),
      Expanded(
        child: Slider(value: value, divisions: 10, onChanged: onChanged),
      ),
      SizedBox(width: 38, child: Text('${(value * 100).round()}%')),
    ],
  );
}

class _Overview extends StatelessWidget {
  const _Overview({
    required this.session,
    required this.onRecord,
    required this.onUpload,
    required this.onAnalyze,
    required this.onQuiz,
  });
  final Map<String, dynamic> session;
  final VoidCallback onRecord;
  final VoidCallback onUpload;
  final VoidCallback onAnalyze;
  final VoidCallback onQuiz;

  @override
  Widget build(BuildContext context) {
    final debts = List<Map<String, dynamic>>.from(
      session['debts'] as List? ?? const [],
    );
    final open = debts.where((item) => item['status'] != 'mastered').length;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Card(
          child: Padding(
            padding: const EdgeInsets.all(20),
            child: Column(
              children: [
                ScoreMeter(
                  label: '课堂还原度',
                  value: session['reconstruction_score'] as int? ?? 0,
                ),
                const SizedBox(height: 16),
                ScoreMeter(
                  label: '学习资料完备度',
                  value: session['learning_coverage'] as int? ?? 0,
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 12),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(20),
            child: Row(
              children: [
                Icon(
                  open == 0 && debts.isNotEmpty
                      ? Icons.check_circle
                      : Icons.bolt,
                  size: 34,
                ),
                const SizedBox(width: 14),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        debts.isEmpty
                            ? '尚未建立知识债务'
                            : open == 0
                            ? '本节债务已清零'
                            : '$open 项知识债务',
                        style: Theme.of(context).textTheme.titleMedium
                            ?.copyWith(fontWeight: FontWeight.w800),
                      ),
                      Text(
                        debts.isEmpty
                            ? '分析课堂后将创建可验收的 Knowledge Point。'
                            : '只有通过掌握验收，Session 才会完成。',
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 20),
        Wrap(
          spacing: 10,
          runSpacing: 10,
          children: [
            FilledButton.icon(
              onPressed: onRecord,
              icon: const Icon(Icons.mic),
              label: const Text('开始课堂记录'),
            ),
            OutlinedButton.icon(
              onPressed: onUpload,
              icon: const Icon(Icons.upload_file),
              label: const Text('添加资料'),
            ),
            OutlinedButton.icon(
              onPressed: onAnalyze,
              icon: const Icon(Icons.auto_awesome),
              label: const Text('分析课堂'),
            ),
            if (debts.isNotEmpty)
              FilledButton.tonalIcon(
                onPressed: onQuiz,
                icon: const Icon(Icons.quiz_outlined),
                label: const Text('极速验收'),
              ),
          ],
        ),
        if ((session['resources'] as List? ?? const []).isEmpty) ...[
          const SizedBox(height: 24),
          const EmptyState(
            icon: Icons.inventory_2_outlined,
            title: '没有资料也不是错误',
            message: 'Session 代表课堂本身。你可以稍后补充录音、PPT、教材，或者先记录你已知的信息。',
          ),
        ],
      ],
    );
  }
}

class _Resources extends StatelessWidget {
  const _Resources({
    required this.session,
    required this.onUpload,
    required this.onChanged,
  });
  final Map<String, dynamic> session;
  final VoidCallback onUpload;
  final Future<void> Function() onChanged;

  Future<void> _transcribe(
    BuildContext context,
    Map<String, dynamic> resource,
  ) async {
    final api = AppScope.of(context).api;
    if (!await confirmExternalUpload(context, '转写录音')) return;
    try {
      await api.transcribe(resource['id'] as String);
      await onChanged();
    } on Object catch (error) {
      if (context.mounted) showError(context, error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final resources = List<Map<String, dynamic>>.from(
      session['resources'] as List? ?? const [],
    );
    if (resources.isEmpty) {
      return EmptyState(
        icon: Icons.folder_open,
        title: '本节还没有资料',
        message: '无录音是合法场景。可以上传 PPT、教材或作业继续还原。',
        action: FilledButton.icon(
          onPressed: onUpload,
          icon: const Icon(Icons.add),
          label: const Text('添加资料'),
        ),
      );
    }
    return ListView.separated(
      padding: const EdgeInsets.all(16),
      itemCount: resources.length + 1,
      separatorBuilder: (_, _) => const SizedBox(height: 10),
      itemBuilder: (context, index) {
        if (index == resources.length) {
          return OutlinedButton.icon(
            onPressed: onUpload,
            icon: const Icon(Icons.add),
            label: const Text('继续添加资料'),
          );
        }
        final resource = resources[index];
        final transcribable =
            resource['type'] == 'audio' || resource['type'] == 'video';
        final processed = resource['upload_state'] == 'processed';
        return Card(
          child: ListTile(
            contentPadding: const EdgeInsets.symmetric(
              horizontal: 18,
              vertical: 8,
            ),
            leading: Icon(_resourceIcon(resource['type'] as String)),
            title: Text(
              resource['name'] as String,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
            subtitle: Text(
              '${resource['evidence_level']} · ${resource['upload_state']}',
            ),
            trailing: transcribable && !processed
                ? TextButton(
                    onPressed: () => _transcribe(context, resource),
                    child: const Text('转写'),
                  )
                : processed
                ? const Icon(Icons.check_circle_outline)
                : null,
          ),
        );
      },
    );
  }

  IconData _resourceIcon(String type) => switch (type) {
    'audio' => Icons.audio_file,
    'video' => Icons.video_file,
    'slides' => Icons.slideshow,
    'textbook' => Icons.menu_book,
    'assignment' => Icons.assignment_outlined,
    _ => Icons.description_outlined,
  };
}

class _Reconstruction extends StatelessWidget {
  const _Reconstruction({required this.session, required this.onAnalyze});
  final Map<String, dynamic> session;
  final VoidCallback onAnalyze;

  @override
  Widget build(BuildContext context) {
    final reconstruction = session['reconstruction'] as Map<String, dynamic>?;
    if (reconstruction == null) {
      return EmptyState(
        icon: Icons.history_edu_outlined,
        title: '课堂尚未还原',
        message: '系统会区分已确认、推测与补充内容，不会用外部资料冒充老师讲过的内容。',
        action: FilledButton.icon(
          onPressed: onAnalyze,
          icon: const Icon(Icons.auto_awesome),
          label: const Text('开始分析'),
        ),
      );
    }
    final timeline = List<Map<String, dynamic>>.from(
      reconstruction['timeline'] as List? ?? const [],
    );
    final confirmed = List<dynamic>.from(
      reconstruction['confirmed'] as List? ?? const [],
    );
    final inferred = List<dynamic>.from(
      reconstruction['inferred'] as List? ?? const [],
    );
    return ListView(
      padding: const EdgeInsets.all(18),
      children: [
        Text(
          reconstruction['title'] as String,
          style: Theme.of(context).textTheme.headlineSmall
              ?.copyWith(fontWeight: FontWeight.w800),
        ),
        const SizedBox(height: 8),
        Text(reconstruction['summary'] as String),
        const SizedBox(height: 24),
        _EvidenceList(
          title: '已确认',
          icon: Icons.verified_outlined,
          items: confirmed,
        ),
        _EvidenceList(title: '推测', icon: Icons.help_outline, items: inferred),
        if (timeline.isNotEmpty) ...[
          const SizedBox(height: 18),
          Text(
            '课堂时间轴',
            style: Theme.of(context).textTheme.titleLarge
                ?.copyWith(fontWeight: FontWeight.w800),
          ),
          ...timeline.map(
            (item) => ListTile(
              contentPadding: EdgeInsets.zero,
              leading: const Icon(Icons.play_circle_outline),
              title: Text(item['title']?.toString() ?? ''),
              subtitle: Text(
                '${_time(item['start_time'])}  ${item['summary'] ?? ''}',
              ),
              trailing: Text(item['confidence']?.toString() ?? ''),
            ),
          ),
        ],
      ],
    );
  }

  String _time(dynamic seconds) {
    if (seconds == null) return '';
    final value = (seconds as num).round();
    return '${(value ~/ 60).toString().padLeft(2, '0')}:${(value % 60).toString().padLeft(2, '0')}';
  }
}

class _EvidenceList extends StatelessWidget {
  const _EvidenceList({
    required this.title,
    required this.icon,
    required this.items,
  });
  final String title;
  final IconData icon;
  final List<dynamic> items;

  @override
  Widget build(BuildContext context) {
    if (items.isEmpty) return const SizedBox.shrink();
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(icon),
                const SizedBox(width: 8),
                Text(
                  title,
                  style: const TextStyle(fontWeight: FontWeight.w800),
                ),
              ],
            ),
            const SizedBox(height: 10),
            ...items.map(
              (item) => Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Text('• $item'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _LearningPath extends StatelessWidget {
  const _LearningPath({
    required this.session,
    required this.onQuiz,
    required this.onChanged,
  });
  final Map<String, dynamic> session;
  final VoidCallback onQuiz;
  final Future<void> Function() onChanged;

  Future<void> _remediate(
    BuildContext context,
    Map<String, dynamic> step,
  ) async {
    final pointIds = List<dynamic>.from(
      step['knowledge_point_ids'] as List? ?? const [],
    );
    if (pointIds.isEmpty) {
      showError(context, '这个步骤暂时没有关联 Knowledge Point');
      return;
    }
    final reason = await showDialog<String>(
      context: context,
      builder: (context) {
        final controller = TextEditingController(
          text: '我没理解，请解释得更基础一点，并给我一个例子。',
        );
        return AlertDialog(
          title: const Text('哪里没懂？'),
          content: TextField(controller: controller, minLines: 2, maxLines: 5),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('取消'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, controller.text),
              child: const Text('换一种方式解释'),
            ),
          ],
        );
      },
    );
    if (reason == null || !context.mounted) return;
    final api = AppScope.of(context).api;
    if (!await confirmExternalUpload(context, '生成针对性补课')) return;
    try {
      final result = await api.remediate(
        knowledgePointId: pointIds.first as String,
        reason: reason,
      );
      if (!context.mounted) return;
      final payload = Map<String, dynamic>.from(result['payload'] as Map);
      await showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        showDragHandle: true,
        builder: (context) => DraggableScrollableSheet(
          expand: false,
          initialChildSize: .78,
          builder: (context, controller) => ListView(
            controller: controller,
            padding: const EdgeInsets.fromLTRB(22, 4, 22, 32),
            children: [
              Text(
                payload['knowledge_point_title'] as String,
                style: Theme.of(context).textTheme.headlineSmall
                    ?.copyWith(fontWeight: FontWeight.w800),
              ),
              const SizedBox(height: 18),
              _RemediationBlock(
                title: '可能卡在这里',
                text: payload['diagnosis'] as String,
              ),
              _RemediationBlock(
                title: '更基础的解释',
                text: payload['simpler_explanation'] as String,
              ),
              _RemediationBlock(
                title: '类比',
                text: payload['analogy'] as String,
              ),
              _RemediationBlock(
                title: '例子',
                text: payload['worked_example'] as String,
              ),
              _RemediationBlock(
                title: '快速自检',
                text: payload['quick_check'] as String,
              ),
            ],
          ),
        ),
      );
    } on Object catch (error) {
      if (context.mounted) showError(context, error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final steps = List<Map<String, dynamic>>.from(
      session['learning_steps'] as List? ?? const [],
    );
    if (steps.isEmpty) {
      return const EmptyState(
        icon: Icons.route_outlined,
        title: '还没有学习路径',
        message: '课堂还原与从零补课是两个不同输出。请先完成课堂分析。',
      );
    }
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        ...steps.map(
          (step) => Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: Card(
              child: ExpansionTile(
                leading: CircleAvatar(child: Text('${step['position']}')),
                title: Text(
                  step['title'] as String,
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
                subtitle: Text(
                  '${step['estimated_minutes']} min · ${step['confidence']}',
                ),
                trailing: step['completed'] == true
                    ? const Icon(Icons.check_circle)
                    : null,
                childrenPadding: const EdgeInsets.fromLTRB(20, 0, 20, 18),
                children: [
                  Align(
                    alignment: Alignment.centerLeft,
                    child: Text(step['full_explanation'] as String),
                  ),
                  const SizedBox(height: 14),
                  SizedBox(
                    width: double.infinity,
                    child: OutlinedButton.icon(
                      onPressed: () => _remediate(context, step),
                      icon: const Icon(Icons.lightbulb_outline),
                      label: const Text('我没懂 · 换一种方式解释'),
                    ),
                  ),
                  const SizedBox(height: 8),
                  SizedBox(
                    width: double.infinity,
                    child: FilledButton.tonalIcon(
                      onPressed: step['completed'] == true
                          ? null
                          : () async {
                              await AppScope.of(context).api
                                  .completeStep(step['id'] as String);
                              await onChanged();
                            },
                      icon: const Icon(Icons.check),
                      label: const Text('我已学完这一步'),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
        const SizedBox(height: 6),
        FilledButton.icon(
          onPressed: onQuiz,
          icon: const Icon(Icons.quiz),
          label: const Text('开始本节验收'),
        ),
        const SizedBox(height: 28),
      ],
    );
  }
}

class _RemediationBlock extends StatelessWidget {
  const _RemediationBlock({required this.title, required this.text});
  final String title;
  final String text;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(bottom: 18),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          title,
          style: Theme.of(context).textTheme.titleMedium
              ?.copyWith(fontWeight: FontWeight.w800),
        ),
        const SizedBox(height: 6),
        Text(text),
      ],
    ),
  );
}
