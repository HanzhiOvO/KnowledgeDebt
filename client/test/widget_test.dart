import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:knowledgedebt/widgets/common.dart';

void main() {
  testWidgets('reconstruction and learning coverage remain separate', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: Column(
            children: [
              ScoreMeter(label: '课堂还原度', value: 45),
              ScoreMeter(label: '学习资料完备度', value: 93),
            ],
          ),
        ),
      ),
    );

    expect(find.text('课堂还原度'), findsOneWidget);
    expect(find.text('45%'), findsOneWidget);
    expect(find.text('学习资料完备度'), findsOneWidget);
    expect(find.text('93%'), findsOneWidget);
  });
}
