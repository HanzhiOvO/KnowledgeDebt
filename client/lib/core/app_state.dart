import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api_client.dart';

class AppState extends ChangeNotifier {
  AppState._(this.preferences, this.api);

  static const _endpointKey = 'api_endpoint';
  static const _homeCacheKey = 'home_cache';
  static const _courseCacheKey = 'course_cache';
  static const _onboardingKey = 'onboarding_complete';

  final SharedPreferences preferences;
  final ApiClient api;

  bool busy = false;
  bool offline = false;
  String? error;
  Map<String, dynamic> home = const {};
  List<Map<String, dynamic>> courses = const [];

  static Future<AppState> load() async {
    final preferences = await SharedPreferences.getInstance();
    final defaultEndpoint = !kIsWeb && Platform.isAndroid
        ? 'http://10.0.2.2:8123'
        : 'http://127.0.0.1:8123';
    final state = AppState._(
      preferences,
      ApiClient(preferences.getString(_endpointKey) ?? defaultEndpoint),
    );
    state._loadCache();
    return state;
  }

  String get endpoint => api.baseUrl;
  bool get onboardingComplete => preferences.getBool(_onboardingKey) ?? false;

  Future<void> completeOnboarding() async {
    await preferences.setBool(_onboardingKey, true);
    notifyListeners();
  }

  void _loadCache() {
    try {
      final cachedHome = preferences.getString(_homeCacheKey);
      final cachedCourses = preferences.getString(_courseCacheKey);
      if (cachedHome != null) {
        home = Map<String, dynamic>.from(jsonDecode(cachedHome) as Map);
      }
      if (cachedCourses != null) {
        courses = (jsonDecode(cachedCourses) as List<dynamic>)
            .map((dynamic item) => Map<String, dynamic>.from(item as Map))
            .toList();
      }
    } on FormatException {
      preferences.remove(_homeCacheKey);
      preferences.remove(_courseCacheKey);
    }
  }

  Future<void> refresh() async {
    busy = true;
    error = null;
    notifyListeners();
    try {
      final results = await Future.wait<dynamic>([api.home(), api.courses()]);
      home = Map<String, dynamic>.from(results[0] as Map);
      courses = List<Map<String, dynamic>>.from(results[1] as List);
      offline = false;
      await preferences.setString(_homeCacheKey, jsonEncode(home));
      await preferences.setString(_courseCacheKey, jsonEncode(courses));
    } on Object catch (exception) {
      offline = true;
      error = exception.toString();
    } finally {
      busy = false;
      notifyListeners();
    }
  }

  Future<void> setEndpoint(String value) async {
    final normalized = value.trim().replaceAll(RegExp(r'/$'), '');
    api.baseUrl = normalized;
    await preferences.setString(_endpointKey, normalized);
    await refresh();
  }
}
