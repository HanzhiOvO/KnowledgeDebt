import 'package:flutter/material.dart';

import '../main.dart';
import '../widgets/common.dart';
import 'session_detail_screen.dart';

class CourseDetailScreen extends StatefulWidget {
  const CourseDetailScreen({required this.courseId, super.key});

  final String courseId;

  @override
  State<CourseDetailScreen> createState() => _CourseDetailScreenState();
}

class _CourseDetailScreenState extends State<CourseDetailScreen> {
  Map<String, dynamic>? course;
  Object? error;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (course == null && error == null) _load();
  }

  Future<void> _load() async {
    try {
      final value = await AppScope.of(context).api.course(widget.courseId);
      if (mounted) setState(() => course = value);
    } on Object catch (exception) {
      if (mounted) setState(() => error = exception);
    }
  }

  Future<void> _createSession() async {
    final title = TextEditingController();
    final notes = TextEditingController();
    final accepted = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('创建 Course Session'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: title,
              autofocus: true,
              decoration: const InputDecoration(
                labelText: '例如 Lecture 12 · 中值定理',
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: notes,
              maxLines: 3,
              decoration: const InputDecoration(labelText: '已知信息 / 缺席说明（可选）'),
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
            child: const Text('创建'),
          ),
        ],
      ),
    );
    if (accepted == true && title.text.trim().isNotEmpty && mounted) {
      final state = AppScope.of(context);
      try {
        final session = await state.api.createSession(
          courseId: widget.courseId,
          title: title.text.trim(),
          notes: notes.text.trim(),
        );
        await state.refresh();
        await _load();
        if (mounted) {
          await Navigator.push(
            context,
            MaterialPageRoute<void>(
              builder: (_) =>
                  SessionDetailScreen(sessionId: session['id'] as String),
            ),
          );
          await _load();
        }
      } on Object catch (exception) {
        if (mounted) showError(context, exception);
      }
    }
  }

  Future<void> _editProfile() async {
    final current = Map<String, dynamic>.from(
      course?['profile'] as Map? ?? const {},
    );
    final weights = current.map(
      (key, value) => MapEntry(key, (value as num).toDouble()),
    );
    const labels = {
      'audio': '课堂录音',
      'video': '课堂视频',
      'slides': 'PPT / 课件',
      'textbook': '指定教材',
      'assignment': '作业 / 习题',
      'syllabus': '教学大纲',
      'note': '课堂笔记',
      'link': '外部资料',
    };
    final accepted = await showDialog<bool>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, setDialogState) => AlertDialog(
          title: const Text('Course Profile'),
          content: SizedBox(
            width: 420,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: labels.entries
                    .map(
                      (item) => Row(
                        children: [
                          SizedBox(width: 78, child: Text(item.value)),
                          Expanded(
                            child: Slider(
                              value: (weights[item.key] ?? 0).clamp(0, 50),
                              max: 50,
                              divisions: 10,
                              onChanged: (value) => setDialogState(
                                () => weights[item.key] = value,
                              ),
                            ),
                          ),
                          SizedBox(
                            width: 30,
                            child: Text('${weights[item.key]?.round() ?? 0}'),
                          ),
                        ],
                      ),
                    )
                    .toList(),
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('取消'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('保存'),
            ),
          ],
        ),
      ),
    );
    if (accepted == true && mounted) {
      try {
        await AppScope.of(context).api
            .updateCourseProfile(widget.courseId, weights);
        await _load();
      } on Object catch (exception) {
        if (mounted) showError(context, exception);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final value = course;
    return Scaffold(
      appBar: AppBar(
        title: Text(value?['name']?.toString() ?? '课程'),
        actions: [
          if (value != null)
            IconButton(
              tooltip: 'Course Profile',
              onPressed: _editProfile,
              icon: const Icon(Icons.tune),
            ),
        ],
      ),
      floatingActionButton: value == null
          ? null
          : FloatingActionButton.extended(
              onPressed: _createSession,
              icon: const Icon(Icons.add),
              label: const Text('新建 Session'),
            ),
      body: value == null
          ? Center(
              child: error == null
                  ? const CircularProgressIndicator()
                  : Text(error.toString()),
            )
          : _CourseBody(course: value),
    );
  }
}

class _CourseBody extends StatelessWidget {
  const _CourseBody({required this.course});
  final Map<String, dynamic> course;

  @override
  Widget build(BuildContext context) {
    final sessions = List<Map<String, dynamic>>.from(
      course['sessions'] as List? ?? const [],
    );
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 100),
      children: [
        Text(
          course['description']?.toString().isNotEmpty == true
              ? course['description'].toString()
              : '这门课程还没有描述。',
        ),
        const SizedBox(height: 20),
        Text(
          'Course Sessions',
          style: Theme.of(context).textTheme.titleLarge
              ?.copyWith(fontWeight: FontWeight.w800),
        ),
        const SizedBox(height: 10),
        if (sessions.isEmpty)
          const Card(
            child: Padding(
              padding: EdgeInsets.all(24),
              child: Text('还没有 Session。没有录音、PPT 或笔记也可以先创建课堂本身。'),
            ),
          )
        else
          ...sessions.map(
            (session) => Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: Card(
                child: ListTile(
                  contentPadding: const EdgeInsets.symmetric(
                    horizontal: 18,
                    vertical: 8,
                  ),
                  title: Text(
                    session['title'] as String,
                    style: const TextStyle(fontWeight: FontWeight.w700),
                  ),
                  subtitle: Text(
                    '还原 ${session['reconstruction_score']}%  ·  学习资料 ${session['learning_coverage']}%',
                  ),
                  leading: Icon(
                    session['status'] == 'complete'
                        ? Icons.check_circle
                        : Icons.radio_button_unchecked,
                  ),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => Navigator.push(
                    context,
                    MaterialPageRoute<void>(
                      builder: (_) => SessionDetailScreen(
                        sessionId: session['id'] as String,
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
      ],
    );
  }
}
