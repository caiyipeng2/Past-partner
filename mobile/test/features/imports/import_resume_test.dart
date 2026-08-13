import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:past_partner/features/imports/import_file.dart';
import 'package:past_partner/features/imports/import_resume.dart';

void main() {
  test('resume manifest round trips only bounded file references', () {
    const ImportUploadResume manifest = ImportUploadResume(
      importId: 'import-1',
      personaId: 'persona-1',
      files: <ImportResumeFile>[
        ImportResumeFile(
          path: 'C:/Users/example/chat.txt',
          sourceName: 'chat.txt',
          mediaType: 'text/plain',
          length: 12,
        ),
      ],
    );

    final ImportUploadResume decoded =
        ImportUploadResume.fromJson(jsonDecode(jsonEncode(manifest.toJson())));

    expect(decoded.importId, 'import-1');
    expect(decoded.personaId, 'persona-1');
    expect(decoded.files.single.path, 'C:/Users/example/chat.txt');
    expect(decoded.files.single.toLocalFile(), isA<RandomAccessImportFile>());
    expect(manifest.toJson().keys, containsAll(<String>[
      'schema_version',
      'import_id',
      'persona_id',
      'files',
    ]));
    expect(manifest.toJson().toString(), isNot(contains('token')));
  });

  test('resume manifest rejects invalid version and file metadata', () {
    expect(
      () => ImportUploadResume.fromJson(<String, dynamic>{
        'schema_version': 2,
        'import_id': 'import-1',
        'persona_id': 'persona-1',
        'files': <dynamic>[],
      }),
      throwsA(isA<ImportResumeError>()),
    );
    expect(
      () => ImportResumeFile.fromJson(<String, dynamic>{
        'path': '',
        'source_name': 'chat.txt',
        'media_type': 'text/plain',
        'length': -1,
      }),
      throwsA(isA<ImportResumeError>()),
    );
  });

  test('in-memory resume store writes reads and deletes by import id', () async {
    final InMemoryImportResumeStore store = InMemoryImportResumeStore();
    const ImportUploadResume manifest = ImportUploadResume(
      importId: 'import-1',
      personaId: 'persona-1',
      files: <ImportResumeFile>[],
    );

    await store.write(manifest);
    expect((await store.read('import-1'))?.personaId, 'persona-1');
    await store.delete('import-1');
    expect(await store.read('import-1'), isNull);
  });

  test('memory files are not persisted as resumable local references', () {
    final List<LocalImportFile> files = <LocalImportFile>[
      MemoryImportFile(
        sourceName: 'chat.txt',
        mediaType: 'text/plain',
        bytes: <int>[1, 2, 3],
      ),
    ];
    expect(ImportResumeFile.fromLocalFile(files.single), isNull);
  });
}
