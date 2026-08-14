import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

class ApiException implements Exception {
  ApiException(this.message, [this.statusCode]);

  final String message;
  final int? statusCode;

  @override
  String toString() => message;
}

class ApiClient {
  ApiClient(this.baseUrl);

  String baseUrl;

  Uri _uri(String path, [Map<String, String>? query]) =>
      Uri.parse('${baseUrl.replaceAll(RegExp(r'/$'), '')}$path')
          .replace(queryParameters: query);

  dynamic _decode(http.Response response) {
    final dynamic body = response.body.isEmpty
        ? <String, dynamic>{}
        : jsonDecode(utf8.decode(response.bodyBytes));
    if (response.statusCode < 200 || response.statusCode >= 300) {
      final message = body is Map<String, dynamic>
          ? body['detail']?.toString() ?? '请求失败'
          : '请求失败';
      throw ApiException(message, response.statusCode);
    }
    return body;
  }

  Future<Map<String, dynamic>> health() async =>
      Map<String, dynamic>.from(_decode(await http.get(_uri('/health'))));

  Future<Map<String, dynamic>> home() async =>
      Map<String, dynamic>.from(_decode(await http.get(_uri('/home'))));

  Future<List<Map<String, dynamic>>> courses() async =>
      (List<dynamic>.from(_decode(await http.get(_uri('/courses')))))
          .map((dynamic item) => Map<String, dynamic>.from(item as Map))
          .toList();

  Future<Map<String, dynamic>> createCourse({
    required String name,
    String semester = '',
    String description = '',
  }) async => Map<String, dynamic>.from(
    _decode(
      await http.post(
        _uri('/courses'),
        headers: const {'Content-Type': 'application/json'},
        body: jsonEncode({
          'name': name,
          'semester': semester,
          'description': description,
        }),
      ),
    ),
  );

  Future<Map<String, dynamic>> course(String id) async =>
      Map<String, dynamic>.from(_decode(await http.get(_uri('/courses/$id'))));

  Future<Map<String, dynamic>> updateCourseProfile(
    String id,
    Map<String, double> profile,
  ) async => Map<String, dynamic>.from(
    _decode(
      await http.patch(
        _uri('/courses/$id/profile'),
        headers: const {'Content-Type': 'application/json'},
        body: jsonEncode({'profile': profile}),
      ),
    ),
  );

  Future<Map<String, dynamic>> createSession({
    required String courseId,
    required String title,
    String notes = '',
  }) async => Map<String, dynamic>.from(
    _decode(
      await http.post(
        _uri('/courses/$courseId/sessions'),
        headers: const {'Content-Type': 'application/json'},
        body: jsonEncode({'title': title, 'notes': notes}),
      ),
    ),
  );

  Future<Map<String, dynamic>> session(String id) async =>
      Map<String, dynamic>.from(_decode(await http.get(_uri('/sessions/$id'))));

  Future<Map<String, dynamic>> uploadResource({
    required String sessionId,
    required String filePath,
    required String type,
    required String evidenceLevel,
    double? durationSeconds,
    double coverage = 1,
    double quality = 1,
    double relevance = 1,
  }) async {
    final request =
        http.MultipartRequest(
            'POST',
            _uri('/sessions/$sessionId/resources/upload'),
          )
          ..fields['resource_type'] = type
          ..fields['evidence_level'] = evidenceLevel
          ..fields['coverage'] = coverage.toString()
          ..fields['quality'] = quality.toString()
          ..fields['relevance'] = relevance.toString();
    if (durationSeconds != null) {
      request.fields['duration_seconds'] = durationSeconds.toString();
    }
    request.files.add(await http.MultipartFile.fromPath('file', filePath));
    final streamed = await request.send();
    final response = await http.Response.fromStream(streamed);
    return Map<String, dynamic>.from(_decode(response));
  }

  Future<void> transcribe(String resourceId) async {
    _decode(
      await http.post(
        _uri('/resources/$resourceId/transcribe'),
        headers: const {'Content-Type': 'application/json'},
        body: jsonEncode({'confirm_external_upload': true}),
      ),
    );
  }

  Future<Map<String, dynamic>> analyze(String sessionId) async =>
      Map<String, dynamic>.from(
        _decode(
          await http.post(
            _uri('/sessions/$sessionId/analyze'),
            headers: const {'Content-Type': 'application/json'},
            body: jsonEncode({'confirm_external_upload': true}),
          ),
        ),
      );

  Future<List<Map<String, dynamic>>> createAssessment(String sessionId) async =>
      (List<dynamic>.from(
        _decode(
          await http.post(
            _uri('/sessions/$sessionId/assessment'),
            headers: const {'Content-Type': 'application/json'},
            body: jsonEncode({'confirm_external_upload': true}),
          ),
        ),
      )).map((dynamic item) => Map<String, dynamic>.from(item as Map)).toList();

  Future<List<Map<String, dynamic>>> assessment(String sessionId) async =>
      (List<dynamic>.from(
        _decode(await http.get(_uri('/sessions/$sessionId/assessment'))),
      )).map((dynamic item) => Map<String, dynamic>.from(item as Map)).toList();

  Future<Map<String, dynamic>> answer({
    required String questionId,
    required String answer,
  }) async => Map<String, dynamic>.from(
    _decode(
      await http.post(
        _uri('/questions/$questionId/answer'),
        headers: const {'Content-Type': 'application/json'},
        body: jsonEncode({'answer': answer, 'confirm_external_upload': true}),
      ),
    ),
  );

  Future<void> completeStep(String stepId) async {
    _decode(await http.post(_uri('/learning-steps/$stepId/complete')));
  }

  Future<Map<String, dynamic>> remediate({
    required String knowledgePointId,
    required String reason,
  }) async => Map<String, dynamic>.from(
    _decode(
      await http.post(
        _uri('/knowledge-points/$knowledgePointId/remediation'),
        headers: const {'Content-Type': 'application/json'},
        body: jsonEncode({'reason': reason, 'confirm_external_upload': true}),
      ),
    ),
  );

  Future<Map<String, dynamic>> providerSettings() async =>
      Map<String, dynamic>.from(
        _decode(await http.get(_uri('/settings/provider'))),
      );

  Future<List<Map<String, dynamic>>> debts() async =>
      (List<dynamic>.from(_decode(await http.get(_uri('/debts')))))
          .map((dynamic item) => Map<String, dynamic>.from(item as Map))
          .toList();

  Future<bool> localFileExists(String path) => File(path).exists();
}
