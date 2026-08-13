import 'package:flutter/material.dart';

import '../persona/persona.dart';
import 'import_controller.dart';
import 'import_file.dart';
import 'import_job.dart';
import 'import_upload_controller.dart';

class ImportWorkspaceScreen extends StatefulWidget {
  const ImportWorkspaceScreen(
      {required this.persona,
      required this.controller,
      this.fileSource,
      this.uploadControllerFactory,
      super.key});

  final Persona persona;
  final ImportController controller;
  final ImportFileSource? fileSource;
  final ImportUploadController Function(ImportJob? job)? uploadControllerFactory;

  @override
  State<ImportWorkspaceScreen> createState() => _ImportWorkspaceScreenState();
}

class _ImportWorkspaceScreenState extends State<ImportWorkspaceScreen> {
  @override
  void initState() {
    super.initState();
    widget.controller.load();
  }

  Future<void> _openCreateForm() async {
    final ImportFileSource? fileSource = widget.fileSource;
    if (fileSource != null && widget.uploadControllerFactory != null) {
      await _pickAndUpload(fileSource);
      return;
    }
    final ImportDraft? draft = await showModalBottomSheet<ImportDraft>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (BuildContext context) => const _ImportFormSheet(),
    );
    if (!mounted || draft == null) return;
    await widget.controller.create(
      ImportDraft(
        personaId: widget.persona.id,
        sourceName: draft.sourceName,
        totalBytes: draft.totalBytes,
        mediaType: draft.mediaType,
      ),
    );
  }

  Future<void> _pickAndUpload(ImportFileSource source,
      {ImportJob? existingJob}) async {
    try {
      final List<LocalImportFile> files = await source.pick();
      if (!mounted || files.isEmpty) return;
      final ImportUploadController Function(ImportJob? job)? factory =
          widget.uploadControllerFactory;
      if (factory == null) return;
      final ImportUploadController uploader = factory(existingJob);
      await uploader.upload(files, existingJob: existingJob);
      if (!mounted) return;
      await widget.controller.load();
      if (!mounted) return;
      if (uploader.errorMessage != null) {
        ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text(uploader.errorMessage!)));
      }
    } on ImportFileError catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.message)));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: widget.controller,
      builder: (BuildContext context, Widget? child) {
        final bool loading = widget.controller.state == ImportStateView.loading;
        final bool saving = widget.controller.state == ImportStateView.saving;
        return Scaffold(
          appBar: AppBar(
            title: Text('${widget.persona.displayName}的导入'),
            actions: <Widget>[
              IconButton(
                tooltip: '刷新导入任务',
                onPressed: loading || saving ? null : widget.controller.load,
                icon: const Icon(Icons.refresh_rounded),
              ),
            ],
          ),
          body: SafeArea(
            child: loading && widget.controller.jobs.isEmpty
                ? const Center(child: CircularProgressIndicator())
                : RefreshIndicator(
                    onRefresh: widget.controller.load,
                    child: widget.controller.jobs.isEmpty
                        ? _EmptyImports(
                            errorMessage: widget.controller.errorMessage,
                            onCreate: _openCreateForm,
                            onRetry: widget.controller.load,
                            canPickFiles: widget.fileSource != null &&
                                widget.uploadControllerFactory != null,
                          )
                        : ListView(
                            padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
                            children: <Widget>[
                              Text('原始资料',
                                  style:
                                      Theme.of(context).textTheme.titleMedium),
                              const SizedBox(height: 4),
                              Text('当前只登记导入任务，下一步再选择文件并上传。',
                                  style: TextStyle(
                                      color: Theme.of(context)
                                          .colorScheme
                                          .onSurfaceVariant)),
                              const SizedBox(height: 16),
                              ...widget.controller.jobs.map((ImportJob job) =>
                                  _ImportCard(
                                      job: job,
                                      onTap: widget.fileSource == null ||
                                              widget.uploadControllerFactory ==
                                                  null ||
                                              job.state == ImportState.uploaded ||
                                              job.state == ImportState.completed
                                          ? null
                                          : () => _pickAndUpload(
                                              widget.fileSource!,
                                              existingJob: job))),
                              const SizedBox(height: 8),
                              OutlinedButton.icon(
                                onPressed: saving ? null : _openCreateForm,
                                icon: const Icon(Icons.add_rounded),
                                label: Text(widget.fileSource != null
                                    ? '选择更多文件'
                                    : '创建另一个任务'),
                              ),
                            ],
                          ),
                  ),
          ),
          floatingActionButton: widget.controller.jobs.isNotEmpty
              ? FloatingActionButton.extended(
                  onPressed: saving ? null : _openCreateForm,
                  icon: const Icon(Icons.upload_file_rounded),
                  label: Text(widget.fileSource != null ? '选择文件' : '创建导入任务'),
                )
              : null,
        );
      },
    );
  }
}

class _EmptyImports extends StatelessWidget {
  const _EmptyImports(
      {required this.errorMessage,
      required this.onCreate,
      required this.onRetry,
      required this.canPickFiles});

  final String? errorMessage;
  final VoidCallback onCreate;
  final VoidCallback onRetry;
  final bool canPickFiles;

  @override
  Widget build(BuildContext context) {
    final bool hasError = errorMessage != null;
    return ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.all(24),
      children: <Widget>[
        const SizedBox(height: 72),
        Icon(hasError ? Icons.cloud_off_rounded : Icons.folder_open_rounded,
            size: 56, color: Theme.of(context).colorScheme.primary),
        const SizedBox(height: 16),
        Text(hasError ? errorMessage! : '还没有导入任务',
            textAlign: TextAlign.center,
            style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w700)),
        const SizedBox(height: 8),
        Text(
            hasError
                ? '请确认服务连接正常后重试。'
                : canPickFiles
                    ? '选择微信、QQ、图片、音频或其他文件，开始分片上传。'
                    : '先登记一份聊天资料，下一步可上传文件并查看处理进度。',
            textAlign: TextAlign.center,
            style: TextStyle(
                color: Theme.of(context).colorScheme.onSurfaceVariant,
                height: 1.4)),
        const SizedBox(height: 24),
        FilledButton.icon(
          key: const Key('create-import-task-button'),
          onPressed: hasError ? onRetry : onCreate,
          icon: Icon(
              hasError ? Icons.refresh_rounded : Icons.upload_file_rounded),
          label: Text(
              hasError ? '重试加载' : canPickFiles ? '选择文件并上传' : '创建导入任务'),
        ),
      ],
    );
  }
}

class _ImportCard extends StatelessWidget {
  const _ImportCard({required this.job, this.onTap});

  final ImportJob job;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        leading: CircleAvatar(child: Icon(_iconFor(job.mediaType))),
        title: Text(job.sourceName,
            style: const TextStyle(fontWeight: FontWeight.w700)),
        subtitle:
            Text('${job.state.label} · ${job.progressLabel}\n${job.mediaType}'),
        isThreeLine: true,
        onTap: onTap,
        trailing: onTap != null
            ? const Icon(Icons.arrow_forward_rounded)
            : null,
      ),
    );
  }

  static IconData _iconFor(String mediaType) {
    if (mediaType.startsWith('image/')) return Icons.image_outlined;
    if (mediaType.startsWith('audio/')) return Icons.audio_file_outlined;
    if (mediaType.startsWith('video/')) return Icons.video_file_outlined;
    return Icons.description_outlined;
  }
}

class _ImportFormSheet extends StatefulWidget {
  const _ImportFormSheet();

  @override
  State<_ImportFormSheet> createState() => _ImportFormSheetState();
}

class _ImportFormSheetState extends State<_ImportFormSheet> {
  final GlobalKey<FormState> _formKey = GlobalKey<FormState>();
  final TextEditingController _sourceController = TextEditingController();
  final TextEditingController _mediaController = TextEditingController();
  final TextEditingController _bytesController = TextEditingController();

  @override
  void dispose() {
    _sourceController.dispose();
    _mediaController.dispose();
    _bytesController.dispose();
    super.dispose();
  }

  void _submit() {
    if (!(_formKey.currentState?.validate() ?? false)) return;
    Navigator.of(context).pop(ImportDraft(
      personaId: '',
      sourceName: _sourceController.text,
      totalBytes: int.parse(_bytesController.text.trim()),
      mediaType: _mediaController.text,
    ));
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(
          left: 20,
          right: 20,
          top: 12,
          bottom: MediaQuery.viewInsetsOf(context).bottom + 20),
      child: Form(
        key: _formKey,
        child: SingleChildScrollView(
          child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: <Widget>[
                Center(
                    child: Container(
                        width: 40,
                        height: 4,
                        decoration: BoxDecoration(
                            color: Theme.of(context).colorScheme.outlineVariant,
                            borderRadius: BorderRadius.circular(2)))),
                const SizedBox(height: 16),
                const Text('创建导入任务',
                    style:
                        TextStyle(fontSize: 22, fontWeight: FontWeight.w700)),
                const SizedBox(height: 4),
                Text('暂未选择文件，下一步可上传文件',
                    style: TextStyle(
                        color: Theme.of(context).colorScheme.onSurfaceVariant)),
                const SizedBox(height: 20),
                TextFormField(
                    key: const Key('import-source-name'),
                    controller: _sourceController,
                    textInputAction: TextInputAction.next,
                    decoration: const InputDecoration(
                        labelText: '来源名称',
                        hintText: '例如：微信聊天记录',
                        border: OutlineInputBorder()),
                    validator: (String? value) =>
                        value == null || value.trim().isEmpty
                            ? '请输入来源名称'
                            : null),
                const SizedBox(height: 16),
                TextFormField(
                    key: const Key('import-media-type'),
                    controller: _mediaController,
                    textInputAction: TextInputAction.next,
                    decoration: const InputDecoration(
                        labelText: '媒体类型',
                        hintText: '例如：text/plain、application/zip',
                        border: OutlineInputBorder()),
                    validator: (String? value) =>
                        value == null || value.trim().isEmpty
                            ? '请输入媒体类型'
                            : null),
                const SizedBox(height: 16),
                TextFormField(
                    key: const Key('import-total-bytes'),
                    controller: _bytesController,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(
                        labelText: '预计大小（字节）',
                        hintText: '例如：1024',
                        border: OutlineInputBorder()),
                    validator: (String? value) {
                      final int? bytes = int.tryParse(value?.trim() ?? '');
                      return bytes == null || bytes < 0 ? '请输入非负字节数' : null;
                    }),
                const SizedBox(height: 20),
                SizedBox(
                    height: 48,
                    child: FilledButton.icon(
                        key: const Key('save-import-task-button'),
                        onPressed: _submit,
                        icon: const Icon(Icons.check_rounded),
                        label: const Text('保存导入任务'))),
              ]),
        ),
      ),
    );
  }
}
