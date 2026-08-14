import 'package:flutter/material.dart';

import '../main.dart';
import '../widgets/common.dart';
import 'course_detail_screen.dart';

class CoursesScreen extends StatelessWidget {
  const CoursesScreen({super.key});

  Future<void> _create(BuildContext context) async {
    final state = AppScope.of(context);
    final name = TextEditingController();
    final semester = TextEditingController();
    final accepted = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('创建课程'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: name,
              autofocus: true,
              decoration: const InputDecoration(labelText: '课程名称'),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: semester,
              decoration: const InputDecoration(labelText: '学期（可选）'),
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
    if (accepted == true && name.text.trim().isNotEmpty && context.mounted) {
      try {
        await state.api.createCourse(
          name: name.text.trim(),
          semester: semester.text.trim(),
        );
        await state.refresh();
      } on Object catch (error) {
        if (context.mounted) showError(context, error);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = AppScope.of(context);
    return Column(
      children: [
        PageHeading(
          '课程',
          subtitle: '${state.courses.length} 门课',
          trailing: IconButton.filled(
            onPressed: () => _create(context),
            icon: const Icon(Icons.add),
          ),
        ),
        Expanded(
          child: state.courses.isEmpty
              ? EmptyState(
                  icon: Icons.school_outlined,
                  title: '创建第一门课程',
                  message: 'Course 管理一整门课；具体某堂课堂，请创建 Course Session。',
                  action: FilledButton.icon(
                    onPressed: () => _create(context),
                    icon: const Icon(Icons.add),
                    label: const Text('新建课程'),
                  ),
                )
              : RefreshIndicator(
                  onRefresh: state.refresh,
                  child: ListView.separated(
                    padding: const EdgeInsets.fromLTRB(16, 0, 16, 24),
                    itemCount: state.courses.length,
                    separatorBuilder: (_, _) => const SizedBox(height: 10),
                    itemBuilder: (context, index) {
                      final course = state.courses[index];
                      return Card(
                        child: ListTile(
                          contentPadding: const EdgeInsets.symmetric(
                            horizontal: 20,
                            vertical: 10,
                          ),
                          leading: const CircleAvatar(
                            child: Icon(Icons.menu_book_outlined),
                          ),
                          title: Text(
                            course['name'] as String,
                            style: const TextStyle(fontWeight: FontWeight.w700),
                          ),
                          subtitle: Text(
                            course['semester']?.toString().isNotEmpty == true
                                ? course['semester'].toString()
                                : '未设置学期',
                          ),
                          trailing: const Icon(Icons.chevron_right),
                          onTap: () => Navigator.push(
                            context,
                            MaterialPageRoute<void>(
                              builder: (_) => CourseDetailScreen(
                                courseId: course['id'] as String,
                              ),
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
