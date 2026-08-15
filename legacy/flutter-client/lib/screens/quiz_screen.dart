import 'package:flutter/material.dart';

import '../core/api_client.dart';
import '../widgets/common.dart';

class QuizScreen extends StatefulWidget {
  const QuizScreen({required this.sessionId, required this.api, super.key});

  final String sessionId;
  final ApiClient api;

  @override
  State<QuizScreen> createState() => _QuizScreenState();
}

class _QuizScreenState extends State<QuizScreen> {
  List<Map<String, dynamic>> questions = const [];
  int index = 0;
  final answer = TextEditingController();
  Map<String, dynamic>? result;
  bool busy = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    answer.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      var loaded = await widget.api.assessment(widget.sessionId);
      if (loaded.isEmpty) {
        loaded = await widget.api.createAssessment(widget.sessionId);
      }
      if (mounted) setState(() => questions = loaded);
    } on Object catch (error) {
      if (mounted) showError(context, error);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> _submit() async {
    if (answer.text.trim().isEmpty) return;
    setState(() => busy = true);
    try {
      final value = await widget.api.answer(
        questionId: questions[index]['id'] as String,
        answer: answer.text.trim(),
      );
      if (mounted) setState(() => result = value);
    } on Object catch (error) {
      if (mounted) showError(context, error);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  void _next() {
    if (index + 1 >= questions.length) {
      Navigator.pop(context, true);
      return;
    }
    setState(() {
      index++;
      result = null;
      answer.clear();
    });
  }

  @override
  Widget build(BuildContext context) {
    final current = questions.isEmpty ? null : questions[index];
    return Scaffold(
      appBar: AppBar(title: const Text('3–5 分钟极速验收')),
      body: busy && current == null
          ? const Center(child: CircularProgressIndicator())
          : current == null
          ? const EmptyState(
              icon: Icons.quiz_outlined,
              title: '暂时无法生成题目',
              message: '请先分析课堂并建立 Knowledge Point。',
            )
          : ListView(
              padding: const EdgeInsets.all(20),
              children: [
                LinearProgressIndicator(value: (index + 1) / questions.length),
                const SizedBox(height: 18),
                Text(
                  '第 ${index + 1} / ${questions.length} 题 · ${current['level']}',
                  style: Theme.of(context).textTheme.labelLarge,
                ),
                const SizedBox(height: 12),
                Text(
                  current['prompt'] as String,
                  style: Theme.of(context).textTheme.headlineSmall
                      ?.copyWith(fontWeight: FontWeight.w800),
                ),
                const SizedBox(height: 24),
                TextField(
                  controller: answer,
                  enabled: result == null,
                  minLines: 4,
                  maxLines: 9,
                  decoration: const InputDecoration(
                    hintText: '写下你的答案。会按含义与关键条件评价，不做纯字符串匹配。',
                  ),
                ),
                const SizedBox(height: 16),
                if (result == null)
                  FilledButton(
                    onPressed: busy ? null : _submit,
                    child: const Text('提交答案'),
                  )
                else
                  _ResultCard(result: result!, onNext: _next),
              ],
            ),
    );
  }
}

class _ResultCard extends StatelessWidget {
  const _ResultCard({required this.result, required this.onNext});
  final Map<String, dynamic> result;
  final VoidCallback onNext;

  @override
  Widget build(BuildContext context) {
    final evaluation = Map<String, dynamic>.from(result['evaluation'] as Map);
    final met = List<dynamic>.from(
      evaluation['met_criteria'] as List? ?? const [],
    );
    final missing = List<dynamic>.from(
      evaluation['missing_criteria'] as List? ?? const [],
    );
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              '${((evaluation['score'] as num) * 100).round()}% · ${evaluation['verdict']}',
              style: Theme.of(context).textTheme.titleLarge
                  ?.copyWith(fontWeight: FontWeight.w800),
            ),
            const SizedBox(height: 10),
            ...met.map((item) => Text('✓ $item')),
            ...missing.map((item) => Text('○ $item')),
            const SizedBox(height: 12),
            Text(evaluation['feedback']?.toString() ?? ''),
            const SizedBox(height: 16),
            SizedBox(
              width: double.infinity,
              child: FilledButton(onPressed: onNext, child: const Text('下一题')),
            ),
          ],
        ),
      ),
    );
  }
}
