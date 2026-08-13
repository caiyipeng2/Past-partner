import 'package:flutter_test/flutter_test.dart';
import 'package:past_partner/features/imports/import_file.dart';

void main() {
  test('reads a selected file in bounded ranges', () async {
    final MemoryImportFile file = MemoryImportFile(
      sourceName: 'chat.txt',
      mediaType: 'text/plain',
      bytes: <int>[0, 1, 2, 3, 4, 5],
    );

    expect(file.length, 6);
    expect(await file.readRange(2, 3), <int>[2, 3, 4]);
    expect(await file.readRange(5, 1), <int>[5]);
  });

  test('rejects ranges outside the selected file', () async {
    final MemoryImportFile file = MemoryImportFile(
      sourceName: 'chat.txt',
      mediaType: 'text/plain',
      bytes: <int>[0, 1],
    );

    expect(() => file.readRange(1, 2), throwsA(isA<ImportFileError>()));
    expect(() => file.readRange(-1, 1), throwsA(isA<ImportFileError>()));
  });

  test('keeps a cancelled selection empty', () async {
    final MemoryImportSource source = MemoryImportSource();
    expect(await source.pick(), isEmpty);
  });
}
