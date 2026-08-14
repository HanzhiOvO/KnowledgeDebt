import 'package:flutter/material.dart';

import 'core/app_state.dart';
import 'core/theme.dart';
import 'screens/shell.dart';
import 'screens/onboarding_screen.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final state = await AppState.load();
  runApp(KnowledgeDebtApp(state: state));
  await state.refresh();
}

class KnowledgeDebtApp extends StatelessWidget {
  const KnowledgeDebtApp({required this.state, super.key});

  final AppState state;

  @override
  Widget build(BuildContext context) => AppScope(
    notifier: state,
    child: MaterialApp(
      title: 'KnowledgeDebt',
      debugShowCheckedModeBanner: false,
      theme: buildTheme(Brightness.light),
      darkTheme: buildTheme(Brightness.dark),
      themeMode: ThemeMode.system,
      home: ListenableBuilder(
        listenable: state,
        builder: (context, _) => state.onboardingComplete
            ? const AppShell()
            : OnboardingScreen(onComplete: state.completeOnboarding),
      ),
    ),
  );
}

class AppScope extends InheritedNotifier<AppState> {
  const AppScope({required super.notifier, required super.child, super.key});

  static AppState of(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<AppScope>()!.notifier!;
}
