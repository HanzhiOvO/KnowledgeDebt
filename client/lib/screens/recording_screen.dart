import 'dart:async';

import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';

import '../core/api_client.dart';
import '../widgets/common.dart';

class RecordingScreen extends StatefulWidget {
  const RecordingScreen({
    required this.sessionId,
    required this.api,
    super.key,
  });

  final String sessionId;
  final ApiClient api;

  @override
  State<RecordingScreen> createState() => _RecordingScreenState();
}

class _RecordingScreenState extends State<RecordingScreen> {
  final recorder = AudioRecorder();
  Timer? timer;
  Timer? safetyTimer;
  Duration elapsed = Duration.zero;
  bool recording = false;
  bool paused = false;
  bool working = false;
  String? savedPath;
  String? currentPath;
  final segmentPaths = <String>[];
  bool rotating = false;

  @override
  void dispose() {
    timer?.cancel();
    safetyTimer?.cancel();
    recorder.dispose();
    super.dispose();
  }

  Future<void> _start() async {
    if (!await recorder.hasPermission()) {
      if (mounted) showError(context, '没有麦克风权限');
      return;
    }
    final path = await _newPath();
    await recorder.start(
      const RecordConfig(encoder: AudioEncoder.aacLc),
      path: path,
    );
    currentPath = path;
    timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted && !paused) {
        setState(() => elapsed += const Duration(seconds: 1));
      }
    });
    safetyTimer = Timer.periodic(
      const Duration(minutes: 5),
      (_) => _checkpoint(),
    );
    setState(() {
      recording = true;
      savedPath = null;
    });
  }

  Future<String> _newPath() async {
    final root = await getApplicationDocumentsDirectory();
    final directory = await root.createTemp('knowledgedebt-recordings');
    return '${directory.path}/session-${DateTime.now().microsecondsSinceEpoch}.m4a';
  }

  Future<void> _checkpoint() async {
    if (!recording || paused || rotating) return;
    rotating = true;
    try {
      final completed = await recorder.stop();
      if (completed != null) segmentPaths.add(completed);
      final next = await _newPath();
      await recorder.start(
        const RecordConfig(encoder: AudioEncoder.aacLc),
        path: next,
      );
      currentPath = next;
    } finally {
      rotating = false;
    }
  }

  Future<void> _togglePause() async {
    if (paused) {
      await recorder.resume();
    } else {
      await recorder.pause();
    }
    setState(() => paused = !paused);
  }

  Future<void> _stop() async {
    timer?.cancel();
    safetyTimer?.cancel();
    final path = await recorder.stop();
    if (path != null) segmentPaths.add(path);
    setState(() {
      recording = false;
      paused = false;
      savedPath = segmentPaths.isEmpty ? currentPath : segmentPaths.first;
    });
  }

  Future<void> _attach() async {
    if (savedPath == null || segmentPaths.isEmpty) return;
    final accepted = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('添加到 Session？'),
        content: Text(
          '录音已安全分段保存在本机（${segmentPaths.length} 段）。\n\n继续会把副本发送到当前设置中的 KnowledgeDebt 后端；不会发送给 AI。',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('只保留本地'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('添加到 Session'),
          ),
        ],
      ),
    );
    if (accepted != true || !mounted) return;
    setState(() => working = true);
    try {
      final durationPerSegment =
          elapsed.inMilliseconds / 1000 / segmentPaths.length;
      for (final path in segmentPaths) {
        await widget.api.uploadResource(
          sessionId: widget.sessionId,
          filePath: path,
          type: 'audio',
          evidenceLevel: 'classroom',
          durationSeconds: durationPerSegment,
        );
      }
      if (mounted) Navigator.pop(context, true);
    } on Object catch (error) {
      if (mounted) showError(context, error);
    } finally {
      if (mounted) setState(() => working = false);
    }
  }

  String get time {
    final hours = elapsed.inHours.toString().padLeft(2, '0');
    final minutes = (elapsed.inMinutes % 60).toString().padLeft(2, '0');
    final seconds = (elapsed.inSeconds % 60).toString().padLeft(2, '0');
    return '$hours:$minutes:$seconds';
  }

  @override
  Widget build(BuildContext context) => PopScope(
    canPop: !recording,
    onPopInvokedWithResult: (didPop, _) async {
      if (didPop || !recording) return;
      final leave = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('先停止并保存录音？'),
          content: const Text('正在录音。为避免丢失，请停止后再离开。'),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('继续录音'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('停止并保存'),
            ),
          ],
        ),
      );
      if (leave == true) await _stop();
    },
    child: Scaffold(
      appBar: AppBar(title: const Text('课堂记录')),
      body: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          children: [
            const Spacer(),
            AnimatedContainer(
              duration: const Duration(milliseconds: 250),
              width: 132,
              height: 132,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: recording
                    ? Theme.of(context).colorScheme.errorContainer
                    : Theme.of(context).colorScheme.primaryContainer,
              ),
              child: Icon(
                recording ? Icons.graphic_eq : Icons.mic_none,
                size: 58,
              ),
            ),
            const SizedBox(height: 32),
            Text(
              time,
              style: Theme.of(context).textTheme.displaySmall?.copyWith(
                fontWeight: FontWeight.w800,
                fontFeatures: const [FontFeature.tabularFigures()],
              ),
            ),
            const SizedBox(height: 8),
            Text(
              paused
                  ? '已暂停 · 文件仍然安全'
                  : recording
                  ? '正在本地录音'
                  : savedPath == null
                  ? '准备开始'
                  : '录音已保存在本机',
            ),
            const Spacer(),
            if (savedPath != null && !recording)
              SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  onPressed: working ? null : _attach,
                  icon: working
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.add_to_photos_outlined),
                  label: const Text('添加到 Session'),
                ),
              )
            else if (!recording)
              SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  onPressed: _start,
                  icon: const Icon(Icons.mic),
                  label: const Text('开始课堂记录'),
                ),
              )
            else
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      onPressed: _togglePause,
                      icon: Icon(paused ? Icons.play_arrow : Icons.pause),
                      label: Text(paused ? '继续' : '暂停'),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: FilledButton.icon(
                      onPressed: _stop,
                      icon: const Icon(Icons.stop),
                      label: const Text('停止'),
                    ),
                  ),
                ],
              ),
            const SizedBox(height: 16),
            Text(
              '录制前请遵守所在地法律、学校规定和课堂隐私要求，并在必要时获得授权。',
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ],
        ),
      ),
    ),
  );
}
