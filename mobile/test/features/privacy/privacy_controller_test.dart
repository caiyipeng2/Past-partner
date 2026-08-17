import 'package:flutter_test/flutter_test.dart';
import 'package:past_partner/features/privacy/privacy_controller.dart';
import 'package:past_partner/features/privacy/privacy_export.dart';
import 'package:past_partner/features/privacy/privacy_gateway.dart';

class _FakePrivacyGateway implements PrivacyGateway {
  _FakePrivacyGateway(this.summary);

  PrivacyExportSummary summary;
  String? deletedPersonaId;
  Object? exportError;
  Object? deleteError;

  @override
  Future<PrivacyExportSummary> exportData() async {
    if (exportError != null) throw exportError!;
    return summary;
  }

  @override
  Future<void> deletePersona(String personaId) async {
    if (deleteError != null) throw deleteError!;
    deletedPersonaId = personaId;
  }
}

PrivacyExportSummary _summary() => PrivacyExportSummary.fromJson(
      <String, dynamic>{
        'export_version': 1,
        'generated_at': '2026-08-17T00:00:00Z',
        'scope': <String, dynamic>{
          'raw_payloads_included': false,
          'omitted': <String>['raw_import_payloads', 'provider_side_data'],
        },
        'personas': <Map<String, dynamic>>[
          <String, dynamic>{'id': 'persona-1'},
        ],
        'imports': <Map<String, dynamic>>[
          <String, dynamic>{
            'job': <String, dynamic>{'id': 'import-1'}
          },
        ],
        'consents': <Map<String, dynamic>>[
          <String, dynamic>{'id': 'consent-1'},
        ],
        'training_jobs': <Map<String, dynamic>>[],
        'conversations': <Map<String, dynamic>>[
          <String, dynamic>{'id': 'conversation-1'},
        ],
      },
    );

void main() {
  test('loads a count-only export summary without retaining raw records',
      () async {
    final _FakePrivacyGateway gateway = _FakePrivacyGateway(_summary());
    final PrivacyController controller = PrivacyController(gateway: gateway);

    await controller.loadExport();

    expect(controller.state, PrivacyState.ready);
    expect(controller.summary?.personaCount, 1);
    expect(controller.summary?.importCount, 1);
    expect(controller.summary?.conversationCount, 1);
    expect(controller.summary?.rawPayloadsIncluded, isFalse);
    expect(controller.summary?.omitted, contains('raw_import_payloads'));
  });

  test('rejects an unbounded export array before it reaches controller state',
      () {
    final List<Map<String, dynamic>> records =
        List<Map<String, dynamic>>.generate(
      2049,
      (int index) => <String, dynamic>{'id': 'record-$index'},
    );

    expect(
      () => PrivacyExportSummary.fromJson(<String, dynamic>{
        'export_version': 1,
        'generated_at': '2026-08-17T00:00:00Z',
        'scope': <String, dynamic>{
          'raw_payloads_included': false,
          'omitted': <String>['raw_import_payloads'],
        },
        'personas': records,
        'imports': <dynamic>[],
        'consents': <dynamic>[],
        'training_jobs': <dynamic>[],
        'conversations': <dynamic>[],
      }),
      throwsFormatException,
    );
  });

  test('deletes one persona and exposes a retryable error on failure',
      () async {
    final _FakePrivacyGateway gateway = _FakePrivacyGateway(_summary());
    final PrivacyController controller = PrivacyController(gateway: gateway);

    expect(await controller.deletePersona('persona-1'), isTrue);
    expect(gateway.deletedPersonaId, 'persona-1');
    expect(controller.state, PrivacyState.ready);

    gateway.deleteError = StateError('offline');
    expect(await controller.deletePersona('persona-2'), isFalse);
    expect(controller.state, PrivacyState.error);
    expect(controller.errorMessage, isNotNull);
  });
}
