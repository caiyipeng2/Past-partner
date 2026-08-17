import 'package:flutter/material.dart';

import '../persona/persona.dart';
import 'privacy_controller.dart';
import 'privacy_export.dart';

class PrivacyScreen extends StatefulWidget {
  const PrivacyScreen({
    required this.controller,
    required this.personas,
    required this.onPersonaDeleted,
    super.key,
  });

  final PrivacyController controller;
  final List<Persona> personas;
  final Future<void> Function() onPersonaDeleted;

  @override
  State<PrivacyScreen> createState() => _PrivacyScreenState();
}

class _PrivacyScreenState extends State<PrivacyScreen> {
  late List<Persona> _personas;

  @override
  void initState() {
    super.initState();
    _personas = List<Persona>.of(widget.personas);
    widget.controller.loadExport();
  }

  Future<void> _confirmDelete(Persona persona) async {
    final bool? confirmed = await showDialog<bool>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        title: const Text('确认删除人物？'),
        content: const Text(
          '删除后将级联移除该人物的导入、授权和会话记录。此操作不可撤销。',
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('取消'),
          ),
          FilledButton(
            key: const Key('privacy-confirm-delete'),
            style: FilledButton.styleFrom(
              backgroundColor: Theme.of(context).colorScheme.error,
              foregroundColor: Theme.of(context).colorScheme.onError,
            ),
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('确认删除'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    final bool deleted = await widget.controller.deletePersona(persona.id);
    if (!mounted || !deleted) return;
    setState(() {
      _personas = _personas
          .where((Persona item) => item.id != persona.id)
          .toList(growable: false);
    });
    await widget.onPersonaDeleted();
    if (mounted) await widget.controller.loadExport();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: widget.controller,
      builder: (BuildContext context, Widget? child) {
        final PrivacyState state = widget.controller.state;
        final bool loading = state == PrivacyState.loading;
        final bool deleting = state == PrivacyState.deleting;
        final PrivacyExportSummary? summary = widget.controller.summary;
        return Scaffold(
          appBar: AppBar(
            title: const Text('隐私管理'),
            actions: <Widget>[
              IconButton(
                tooltip: '刷新隐私摘要',
                onPressed:
                    loading || deleting ? null : widget.controller.loadExport,
                icon: const Icon(Icons.refresh_rounded),
              ),
            ],
          ),
          body: SafeArea(
            child: ListView(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 32),
              children: <Widget>[
                Text(
                  '只在本地服务范围内管理你的数据',
                  style: Theme.of(context).textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.w700,
                      ),
                ),
                const SizedBox(height: 4),
                Text(
                  '导出操作只读取 owner 范围的元数据摘要，不会把访问令牌或原始消息保存到手机。',
                  style: TextStyle(
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                    height: 1.35,
                  ),
                ),
                const SizedBox(height: 16),
                if (loading && summary == null)
                  const Center(child: CircularProgressIndicator())
                else if (summary != null)
                  _SummaryCard(summary: summary),
                if (widget.controller.errorMessage != null) ...<Widget>[
                  const SizedBox(height: 12),
                  _ErrorBanner(message: widget.controller.errorMessage!),
                ],
                const SizedBox(height: 20),
                OutlinedButton.icon(
                  key: const Key('privacy-refresh-export'),
                  onPressed:
                      loading || deleting ? null : widget.controller.loadExport,
                  icon: const Icon(Icons.file_download_outlined),
                  label: Text(loading ? '正在读取摘要…' : '重新读取导出摘要'),
                ),
                const SizedBox(height: 24),
                Text(
                  '人物数据删除',
                  style: Theme.of(context).textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.w700,
                      ),
                ),
                const SizedBox(height: 4),
                Text(
                  '删除会级联清理该人物的受控导入、授权、训练任务和会话。第三方已经接收的数据不在本地删除范围内。',
                  style: TextStyle(
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                    height: 1.35,
                  ),
                ),
                const SizedBox(height: 12),
                if (_personas.isEmpty)
                  const ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(Icons.check_circle_outline_rounded),
                    title: Text('当前没有可删除的人物'),
                  )
                else
                  ..._personas.map(
                    (Persona persona) => Card(
                      margin: const EdgeInsets.only(bottom: 8),
                      child: ListTile(
                        contentPadding: const EdgeInsets.symmetric(
                          horizontal: 16,
                          vertical: 4,
                        ),
                        leading: CircleAvatar(
                          child: Text(persona.displayName.substring(0, 1)),
                        ),
                        title: Text(persona.displayName),
                        subtitle: Text(persona.relationshipLabel),
                        trailing: IconButton(
                          key: Key('privacy-delete-${persona.id}'),
                          tooltip: '删除人物及关联数据',
                          onPressed:
                              deleting ? null : () => _confirmDelete(persona),
                          icon: const Icon(Icons.delete_outline_rounded),
                        ),
                      ),
                    ),
                  ),
              ],
            ),
          ),
        );
      },
    );
  }
}

class _SummaryCard extends StatelessWidget {
  const _SummaryCard({required this.summary});

  final PrivacyExportSummary summary;

  @override
  Widget build(BuildContext context) {
    final ColorScheme colors = Theme.of(context).colorScheme;
    final bool safe = !summary.rawPayloadsIncluded;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Icon(
                  safe ? Icons.verified_user_outlined : Icons.warning_amber,
                  color: safe ? colors.primary : colors.error,
                ),
                const SizedBox(width: 8),
                Text(
                  safe ? '原始内容未包含在导出摘要中' : '导出范围需要复核',
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Text(
              '人物 ${summary.personaCount} · 导入 ${summary.importCount} · 会话 ${summary.conversationCount}',
            ),
            const SizedBox(height: 4),
            Text(
              '授权 ${summary.consentCount} · 训练任务 ${summary.trainingJobCount}',
              style: TextStyle(color: colors.onSurfaceVariant),
            ),
            const SizedBox(height: 12),
            Text(
              '未包含：${summary.omitted.isEmpty ? '无' : summary.omitted.join('、')}',
              style: TextStyle(color: colors.onSurfaceVariant),
            ),
          ],
        ),
      ),
    );
  }
}

class _ErrorBanner extends StatelessWidget {
  const _ErrorBanner({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    final ColorScheme colors = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: colors.errorContainer,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Text(
        message,
        style: TextStyle(color: colors.onErrorContainer),
      ),
    );
  }
}
