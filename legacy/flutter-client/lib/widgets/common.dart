import 'package:flutter/material.dart';

import '../core/theme.dart';

class PageHeading extends StatelessWidget {
  const PageHeading(this.title, {this.subtitle, this.trailing, super.key});

  final String title;
  final String? subtitle;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(20, 22, 20, 18),
    child: Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                title,
                style: Theme.of(context).textTheme.headlineMedium
                    ?.copyWith(fontWeight: FontWeight.w800),
              ),
              if (subtitle != null) ...[
                const SizedBox(height: 4),
                Text(
                  subtitle!,
                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
                ),
              ],
            ],
          ),
        ),
        trailing ?? const SizedBox.shrink(),
      ],
    ),
  );
}

class ScoreMeter extends StatelessWidget {
  const ScoreMeter({required this.label, required this.value, super.key});

  final String label;
  final int value;

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label, style: Theme.of(context).textTheme.labelMedium),
          Text('$value%', style: const TextStyle(fontWeight: FontWeight.w800)),
        ],
      ),
      const SizedBox(height: 6),
      LinearProgressIndicator(
        value: value / 100,
        minHeight: 7,
        borderRadius: BorderRadius.circular(8),
      ),
    ],
  );
}

class DebtBadge extends StatelessWidget {
  const DebtBadge(this.count, {this.pending = false, super.key});

  final int count;
  final bool pending;

  @override
  Widget build(BuildContext context) => DecoratedBox(
    decoration: BoxDecoration(
      color: count == 0 && !pending
          ? moss.withValues(alpha: .12)
          : debtRed.withValues(alpha: .12),
      borderRadius: BorderRadius.circular(20),
    ),
    child: Padding(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      child: Text(
        pending
            ? '待分析'
            : count == 0
            ? '已清零'
            : '$count 项债务',
        style: TextStyle(
          color: count == 0 && !pending ? moss : debtRed,
          fontWeight: FontWeight.w700,
          fontSize: 12,
        ),
      ),
    ),
  );
}

class EmptyState extends StatelessWidget {
  const EmptyState({
    required this.icon,
    required this.title,
    required this.message,
    this.action,
    super.key,
  });

  final IconData icon;
  final String title;
  final String message;
  final Widget? action;

  @override
  Widget build(BuildContext context) => Center(
    child: Padding(
      padding: const EdgeInsets.all(40),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 44, color: Theme.of(context).colorScheme.primary),
          const SizedBox(height: 16),
          Text(
            title,
            style: Theme.of(context).textTheme.titleLarge
                ?.copyWith(fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 8),
          Text(
            message,
            textAlign: TextAlign.center,
            style: TextStyle(
              color: Theme.of(context).colorScheme.onSurfaceVariant,
            ),
          ),
          if (action != null) ...[const SizedBox(height: 20), action!],
        ],
      ),
    ),
  );
}

Future<bool> confirmExternalUpload(BuildContext context, String action) async =>
    await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        icon: const Icon(Icons.cloud_upload_outlined),
        title: Text('允许上传以$action？'),
        content: const Text(
          '本次操作会把当前 Session 中选定的课堂资料发送给你配置的 AI Provider。录音与文件不会被静默上传。',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('同意并继续'),
          ),
        ],
      ),
    ) ??
    false;

void showError(BuildContext context, Object error) {
  ScaffoldMessenger.of(context)
      .showSnackBar(SnackBar(content: Text(error.toString())));
}
