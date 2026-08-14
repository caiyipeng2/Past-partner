import 'package:flutter/material.dart';

import 'import_review.dart';
import 'import_review_controller.dart';

class ImportReviewScreen extends StatefulWidget {
  const ImportReviewScreen({required this.controller, super.key});

  final ImportReviewController controller;

  @override
  State<ImportReviewScreen> createState() => _ImportReviewScreenState();
}

class _ImportReviewScreenState extends State<ImportReviewScreen> {
  ImportReviewController get _controller => widget.controller;

  @override
  void initState() {
    super.initState();
    _controller.load();
  }

  Future<void> _editRecord(ImportPreviewRecord record) async {
    final ImportCorrection? correction =
        await showModalBottomSheet<ImportCorrection>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (BuildContext context) => _RecordEditSheet(
        record: record,
        initialState: _controller.reviewStateFor(record),
      ),
    );
    if (!mounted || correction == null) return;
    _controller.setCorrection(correction);
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _controller,
      builder: (BuildContext context, Widget? child) {
        final bool busy = _controller.state == ImportReviewState.loading ||
            _controller.state == ImportReviewState.saving;
        return Scaffold(
          appBar: AppBar(
            title: const Text('导入审核'),
            actions: <Widget>[
              IconButton(
                tooltip: '刷新审核数据',
                onPressed: busy ? null : _controller.load,
                icon: const Icon(Icons.refresh_rounded),
              ),
            ],
          ),
          body: SafeArea(
            child: _controller.state == ImportReviewState.loading &&
                    _controller.preview == null
                ? const Center(child: CircularProgressIndicator())
                : RefreshIndicator(
                    onRefresh: _controller.load,
                    child: _body(context),
                  ),
          ),
        );
      },
    );
  }

  Widget _body(BuildContext context) {
    final ImportPreview? preview = _controller.preview;
    if (_controller.state == ImportReviewState.error && preview == null) {
      return ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.all(24),
        children: <Widget>[
          const SizedBox(height: 72),
          Icon(Icons.cloud_off_rounded,
              size: 56, color: Theme.of(context).colorScheme.primary),
          const SizedBox(height: 16),
          Text(_controller.errorMessage ?? '导入审核暂时不可用。',
              textAlign: TextAlign.center,
              style:
                  const TextStyle(fontSize: 22, fontWeight: FontWeight.w700)),
          const SizedBox(height: 20),
          FilledButton.icon(
            onPressed: _controller.load,
            icon: const Icon(Icons.refresh_rounded),
            label: const Text('重试加载'),
          ),
        ],
      );
    }
    if (preview == null) {
      return ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.all(24),
        children: const <Widget>[Center(child: Text('暂无审核数据'))],
      );
    }
    return ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
      children: <Widget>[
        _SummarySection(preview: preview),
        if (preview.warnings.isNotEmpty) ...<Widget>[
          const SizedBox(height: 16),
          _WarningsSection(warnings: preview.warnings),
        ],
        const SizedBox(height: 20),
        _MappingSection(controller: _controller),
        const SizedBox(height: 20),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: <Widget>[
            Text('消息记录',
                style: Theme.of(context)
                    .textTheme
                    .titleMedium
                    ?.copyWith(fontWeight: FontWeight.w700)),
            Text('${preview.records.length} 条预览',
                style: TextStyle(
                    color: Theme.of(context).colorScheme.onSurfaceVariant)),
          ],
        ),
        const SizedBox(height: 8),
        ...preview.records.map((ImportPreviewRecord record) => _RecordCard(
            record: record,
            state: _controller.reviewStateFor(record),
            onEdit: () => _editRecord(record),
            onStateChanged: (ReviewState state) =>
                _controller.setReviewState(record.recordId, state))),
        if (preview.records.isNotEmpty) ...<Widget>[
          const SizedBox(height: 8),
          FilledButton.icon(
            key: const Key('save-corrections-button'),
            onPressed: _controller.state == ImportReviewState.saving
                ? null
                : _controller.saveCorrections,
            icon: const Icon(Icons.check_rounded),
            label: const Text('保存记录审核'),
          ),
        ],
      ],
    );
  }
}

class _SummarySection extends StatelessWidget {
  const _SummarySection({required this.preview});

  final ImportPreview preview;

  @override
  Widget build(BuildContext context) {
    final ImportPreviewSummary summary = preview.summary;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Icon(Icons.fact_check_outlined,
                    color: Theme.of(context).colorScheme.primary),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(preview.sourceName,
                          style: const TextStyle(
                              fontSize: 18, fontWeight: FontWeight.w700)),
                      const SizedBox(height: 4),
                      Text('${preview.sourceType} · ${preview.mediaType}',
                          style: TextStyle(
                              color: Theme.of(context)
                                  .colorScheme
                                  .onSurfaceVariant)),
                    ],
                  ),
                ),
                Chip(label: Text(preview.state)),
              ],
            ),
            const SizedBox(height: 16),
            Wrap(
              spacing: 20,
              runSpacing: 8,
              children: <Widget>[
                _Metric(label: '记录', value: '${summary.recordCount}'),
                _Metric(label: '警告', value: '${summary.warningCount}'),
                if (summary.confidence != null)
                  _Metric(
                      label: '置信度',
                      value:
                          '${(summary.confidence! * 100).toStringAsFixed(0)}%'),
              ],
            ),
            if (summary.truncated) ...<Widget>[
              const SizedBox(height: 12),
              Text('当前仅展示有限预览，完整审核将在服务端处理。',
                  style:
                      TextStyle(color: Theme.of(context).colorScheme.tertiary)),
            ],
          ],
        ),
      ),
    );
  }
}

class _Metric extends StatelessWidget {
  const _Metric({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => RichText(
        text: TextSpan(
          style: DefaultTextStyle.of(context).style,
          children: <InlineSpan>[
            TextSpan(text: '$label ', style: const TextStyle(fontSize: 13)),
            TextSpan(
                text: value,
                style:
                    const TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
          ],
        ),
      );
}

class _WarningsSection extends StatelessWidget {
  const _WarningsSection({required this.warnings});

  final List<String> warnings;

  @override
  Widget build(BuildContext context) => Card(
        color: Theme.of(context).colorScheme.tertiaryContainer,
        child: ExpansionTile(
          leading: const Icon(Icons.warning_amber_rounded),
          title: Text('需要留意的警告（${warnings.length}）'),
          children: warnings
              .map((String warning) => ListTile(
                    dense: true,
                    leading: const Icon(Icons.info_outline_rounded, size: 20),
                    title: Text(warning),
                  ))
              .toList(growable: false),
        ),
      );
}

class _MappingSection extends StatelessWidget {
  const _MappingSection({required this.controller});

  final ImportReviewController controller;

  @override
  Widget build(BuildContext context) {
    final Iterable<String> participantIds = controller.mapping.keys;
    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 14, 16, 16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text('参与者身份',
                style: Theme.of(context)
                    .textTheme
                    .titleMedium
                    ?.copyWith(fontWeight: FontWeight.w700)),
            const SizedBox(height: 4),
            Text('先确认谁是人物、谁是你，模型才会使用正确的说话人。',
                style: TextStyle(
                    color: Theme.of(context).colorScheme.onSurfaceVariant)),
            const SizedBox(height: 12),
            if (participantIds.isEmpty)
              const Text('预览中暂无参与者映射。')
            else
              ...participantIds.map((String participantId) => ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: const Icon(Icons.person_outline_rounded),
                    title: Text(participantId),
                    trailing: DropdownButton<ParticipantRole>(
                      key: Key('participant-role-$participantId'),
                      value: controller.roleFor(participantId),
                      onChanged: (ParticipantRole? role) {
                        if (role != null) {
                          controller.setMapping(participantId, role);
                        }
                      },
                      items: ParticipantRole.values
                          .map((ParticipantRole role) =>
                              DropdownMenuItem<ParticipantRole>(
                                value: role,
                                child: Text(role.label),
                              ))
                          .toList(growable: false),
                    ),
                  )),
            const SizedBox(height: 8),
            SizedBox(
              width: double.infinity,
              child: OutlinedButton.icon(
                key: const Key('save-participant-mapping-button'),
                onPressed: controller.state == ImportReviewState.saving
                    ? null
                    : controller.saveMapping,
                icon: const Icon(Icons.save_outlined),
                label: const Text('保存身份映射'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _RecordCard extends StatelessWidget {
  const _RecordCard({
    required this.record,
    required this.state,
    required this.onEdit,
    required this.onStateChanged,
  });

  final ImportPreviewRecord record;
  final ReviewState state;
  final VoidCallback onEdit;
  final ValueChanged<ReviewState> onStateChanged;

  @override
  Widget build(BuildContext context) => Card(
        margin: const EdgeInsets.only(bottom: 10),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 12, 12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Row(
                children: <Widget>[
                  Expanded(
                    child: Text(record.senderName ?? record.senderId ?? '未知参与者',
                        style: const TextStyle(fontWeight: FontWeight.w700)),
                  ),
                  DropdownButton<ReviewState>(
                    key: Key('review-state-${record.recordId}'),
                    value: state,
                    underline: const SizedBox.shrink(),
                    onChanged: (ReviewState? value) {
                      if (value != null) onStateChanged(value);
                    },
                    items: ReviewState.values
                        .map((ReviewState value) =>
                            DropdownMenuItem<ReviewState>(
                              value: value,
                              child: Text(value.label),
                            ))
                        .toList(growable: false),
                  ),
                  IconButton(
                    tooltip: '编辑记录',
                    onPressed: onEdit,
                    icon: const Icon(Icons.edit_outlined),
                  ),
                ],
              ),
              if (record.timestamp != null) ...<Widget>[
                const SizedBox(height: 2),
                Text(record.timestamp!,
                    style: Theme.of(context).textTheme.bodySmall),
              ],
              if (record.content != null &&
                  record.content!.isNotEmpty) ...<Widget>[
                const SizedBox(height: 10),
                Text(record.content!,
                    maxLines: 8, overflow: TextOverflow.ellipsis),
              ],
              const SizedBox(height: 6),
              Text(record.messageType ?? '未知消息类型',
                  style: Theme.of(context).textTheme.labelSmall),
            ],
          ),
        ),
      );
}

class _RecordEditSheet extends StatefulWidget {
  const _RecordEditSheet({required this.record, required this.initialState});

  final ImportPreviewRecord record;
  final ReviewState initialState;

  @override
  State<_RecordEditSheet> createState() => _RecordEditSheetState();
}

class _RecordEditSheetState extends State<_RecordEditSheet> {
  final GlobalKey<FormState> _formKey = GlobalKey<FormState>();
  late final TextEditingController _senderName =
      TextEditingController(text: widget.record.senderName ?? '');
  late final TextEditingController _content =
      TextEditingController(text: widget.record.content ?? '');
  late final TextEditingController _timestamp =
      TextEditingController(text: widget.record.timestamp ?? '');
  late ReviewState _state = widget.initialState;

  @override
  void dispose() {
    _senderName.dispose();
    _content.dispose();
    _timestamp.dispose();
    super.dispose();
  }

  void _submit() {
    if (!(_formKey.currentState?.validate() ?? false)) return;
    Navigator.of(context).pop(ImportCorrection(
      recordId: widget.record.recordId,
      senderId: widget.record.senderId,
      senderName:
          _senderName.text.trim().isEmpty ? null : _senderName.text.trim(),
      content: _content.text,
      timestamp: _timestamp.text.trim().isEmpty ? null : _timestamp.text.trim(),
      messageType: widget.record.messageType,
      reviewState: _state,
    ));
  }

  @override
  Widget build(BuildContext context) => Padding(
        padding: EdgeInsets.only(
          left: 20,
          right: 20,
          top: 12,
          bottom: MediaQuery.viewInsetsOf(context).bottom + 20,
        ),
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
                      borderRadius: BorderRadius.circular(2),
                    ),
                  ),
                ),
                const SizedBox(height: 16),
                const Text('编辑消息记录',
                    style:
                        TextStyle(fontSize: 22, fontWeight: FontWeight.w700)),
                const SizedBox(height: 16),
                TextFormField(
                  controller: _senderName,
                  decoration: const InputDecoration(
                      labelText: '发送者名称', border: OutlineInputBorder()),
                  maxLength: 256,
                ),
                const SizedBox(height: 12),
                TextFormField(
                  controller: _content,
                  decoration: const InputDecoration(
                      labelText: '消息内容', border: OutlineInputBorder()),
                  maxLines: 4,
                  maxLength: 10000,
                ),
                const SizedBox(height: 12),
                TextFormField(
                  controller: _timestamp,
                  decoration: const InputDecoration(
                      labelText: '时间（可选）', border: OutlineInputBorder()),
                  maxLength: 128,
                ),
                const SizedBox(height: 4),
                DropdownButtonFormField<ReviewState>(
                  initialValue: _state,
                  decoration: const InputDecoration(
                      labelText: '审核状态', border: OutlineInputBorder()),
                  onChanged: (ReviewState? value) {
                    if (value != null) setState(() => _state = value);
                  },
                  items: ReviewState.values
                      .map((ReviewState state) => DropdownMenuItem<ReviewState>(
                          value: state, child: Text(state.label)))
                      .toList(growable: false),
                ),
                const SizedBox(height: 20),
                SizedBox(
                  height: 48,
                  child: FilledButton.icon(
                    onPressed: _submit,
                    icon: const Icon(Icons.check_rounded),
                    label: const Text('保存这条修正'),
                  ),
                ),
              ],
            ),
          ),
        ),
      );
}
