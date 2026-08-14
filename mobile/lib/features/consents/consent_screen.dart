import 'package:flutter/material.dart';

import '../models/model_option.dart';
import 'consent.dart';
import 'consent_controller.dart';

class ConsentScreen extends StatefulWidget {
  const ConsentScreen({
    required this.personaName,
    required this.controller,
    this.selectedModel,
    super.key,
  });

  final String personaName;
  final ConsentController controller;
  final ModelOption? selectedModel;

  @override
  State<ConsentScreen> createState() => _ConsentScreenState();
}

class _ConsentScreenState extends State<ConsentScreen> {
  @override
  void initState() {
    super.initState();
    widget.controller.load();
  }

  Future<void> _openCreateForm() async {
    final ModelOption? model = widget.selectedModel;
    if (model == null || widget.controller.state == ConsentState.saving) return;
    final ConsentDraft? draft = await showModalBottomSheet<ConsentDraft>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (BuildContext context) => _ConsentFormSheet(
        personaId: widget.controller.personaId,
        model: model,
      ),
    );
    if (!mounted || draft == null) return;
    await widget.controller.create(draft);
  }

  Future<void> _confirmRevoke(Consent consent) async {
    final bool? confirmed = await showDialog<bool>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        title: const Text('撤回这项授权？'),
        content: const Text('撤回后，后续第三方媒体处理将不再使用这项授权。'),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('暂不撤回'),
          ),
          FilledButton(
            key: const Key('consent-confirm-revoke'),
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('确认撤回'),
          ),
        ],
      ),
    );
    if (!mounted || confirmed != true) return;
    await widget.controller.revoke(consent);
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: widget.controller,
      builder: (BuildContext context, Widget? child) {
        final ConsentController controller = widget.controller;
        final bool loading = controller.state == ConsentState.loading;
        final bool saving = controller.state == ConsentState.saving;
        final bool hasError = controller.errorMessage != null;
        final ModelOption? model = widget.selectedModel;
        return Scaffold(
          appBar: AppBar(
            title: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                const Text('授权管理'),
                Text(widget.personaName,
                    style: Theme.of(context).textTheme.labelSmall),
              ],
            ),
            actions: <Widget>[
              IconButton(
                tooltip: '刷新授权',
                onPressed: loading || saving ? null : controller.load,
                icon: const Icon(Icons.refresh_rounded),
              ),
            ],
          ),
          body: SafeArea(
            child: ListView(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 32),
              children: <Widget>[
                _ModelSummary(model: model),
                const SizedBox(height: 16),
                Row(
                  crossAxisAlignment: CrossAxisAlignment.center,
                  children: <Widget>[
                    const Expanded(
                      child: Text('第三方处理授权',
                          style: TextStyle(
                              fontSize: 20, fontWeight: FontWeight.w700)),
                    ),
                    FilledButton.icon(
                      key: const Key('consent-create'),
                      onPressed:
                          model == null || saving ? null : _openCreateForm,
                      icon: const Icon(Icons.add_rounded),
                      label: const Text('新增授权'),
                    ),
                  ],
                ),
                const SizedBox(height: 6),
                Text(
                  '只授权你明确选择的数据类别和处理范围。撤回只影响后续处理。',
                  style: TextStyle(
                      color: Theme.of(context).colorScheme.onSurfaceVariant),
                ),
                if (hasError) ...<Widget>[
                  const SizedBox(height: 12),
                  _ErrorNotice(
                    message: controller.errorMessage!,
                    onRetry: saving ? null : controller.load,
                  ),
                ],
                const SizedBox(height: 16),
                if (loading && controller.consents.isEmpty)
                  const Padding(
                    padding: EdgeInsets.symmetric(vertical: 56),
                    child: Center(child: CircularProgressIndicator()),
                  )
                else if (controller.consents.isEmpty)
                  const _EmptyConsentState()
                else
                  ...controller.consents.map(
                    (Consent consent) => Padding(
                      padding: const EdgeInsets.only(bottom: 12),
                      child: _ConsentCard(
                        consent: consent,
                        onRevoke:
                            consent.status == ConsentStatus.active && !saving
                                ? () => _confirmRevoke(consent)
                                : null,
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

class _ModelSummary extends StatelessWidget {
  const _ModelSummary({required this.model});

  final ModelOption? model;

  @override
  Widget build(BuildContext context) {
    if (model == null) {
      return Card(
        color: Theme.of(context).colorScheme.secondaryContainer,
        child: const Padding(
          padding: EdgeInsets.all(16),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Icon(Icons.info_outline_rounded),
              SizedBox(width: 12),
              Expanded(child: Text('请先选择模型，再创建授权。')),
            ],
          ),
        ),
      );
    }
    return Card(
      child: ListTile(
        leading: const CircleAvatar(child: Icon(Icons.auto_awesome_rounded)),
        title: const Text('当前模型'),
        subtitle: Text('${model!.providerName} · ${model!.displayName}'),
        trailing: const Icon(Icons.verified_user_outlined),
      ),
    );
  }
}

class _EmptyConsentState extends StatelessWidget {
  const _EmptyConsentState();

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 28),
        child: Column(
          children: <Widget>[
            Icon(Icons.shield_outlined,
                size: 48, color: Theme.of(context).colorScheme.primary),
            const SizedBox(height: 12),
            const Text('还没有授权',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
            const SizedBox(height: 6),
            Text('当前人物的第三方处理默认关闭。',
                style: TextStyle(
                    color: Theme.of(context).colorScheme.onSurfaceVariant)),
          ],
        ),
      ),
    );
  }
}

class _ErrorNotice extends StatelessWidget {
  const _ErrorNotice({required this.message, required this.onRetry});

  final String message;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    return Card(
      color: Theme.of(context).colorScheme.errorContainer,
      child: ListTile(
        leading: const Icon(Icons.error_outline_rounded),
        title: Text(message),
        trailing: IconButton(
          tooltip: '重试',
          onPressed: onRetry,
          icon: const Icon(Icons.refresh_rounded),
        ),
      ),
    );
  }
}

class _ConsentCard extends StatelessWidget {
  const _ConsentCard({required this.consent, required this.onRevoke});

  final Consent consent;
  final VoidCallback? onRevoke;

  @override
  Widget build(BuildContext context) {
    final bool active = consent.status == ConsentStatus.active;
    final Color statusColor = active
        ? Theme.of(context).colorScheme.primary
        : Theme.of(context).colorScheme.onSurfaceVariant;
    return Semantics(
      container: true,
      label:
          '${consent.dataCategory.label}，${consent.purpose}，${consent.status.label}',
      child: Card(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 8, 12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              Row(
                children: <Widget>[
                  Icon(active ? Icons.shield_rounded : Icons.shield_outlined,
                      color: statusColor),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                        '${consent.dataCategory.label} · ${consent.purpose}',
                        style: const TextStyle(fontWeight: FontWeight.w700)),
                  ),
                  Chip(
                    label: Text(consent.status.label),
                    visualDensity: VisualDensity.compact,
                    side: BorderSide.none,
                    backgroundColor: active
                        ? Theme.of(context).colorScheme.primaryContainer
                        : Theme.of(context).colorScheme.surfaceContainerHighest,
                  ),
                ],
              ),
              const SizedBox(height: 8),
              Text('${consent.providerId} · ${consent.modelId}'),
              const SizedBox(height: 4),
              Text('作用域：${consent.authorizationScope}',
                  style: TextStyle(
                      color: Theme.of(context).colorScheme.onSurfaceVariant)),
              const SizedBox(height: 4),
              Text('预计费用上限：${consent.estimatedCost.toStringAsFixed(4)}',
                  style: TextStyle(
                      color: Theme.of(context).colorScheme.onSurfaceVariant)),
              if (onRevoke != null) ...<Widget>[
                const SizedBox(height: 4),
                Align(
                  alignment: Alignment.centerRight,
                  child: TextButton.icon(
                    key: Key('consent-revoke-${consent.id}'),
                    onPressed: onRevoke,
                    icon: const Icon(Icons.remove_circle_outline_rounded),
                    label: const Text('撤回授权'),
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _ConsentFormSheet extends StatefulWidget {
  const _ConsentFormSheet({required this.personaId, required this.model});

  final String personaId;
  final ModelOption model;

  @override
  State<_ConsentFormSheet> createState() => _ConsentFormSheetState();
}

class _ConsentFormSheetState extends State<_ConsentFormSheet> {
  final GlobalKey<FormState> _formKey = GlobalKey<FormState>();
  late ConsentDataCategory _category;
  late final TextEditingController _purposeController;
  late final TextEditingController _costController;

  @override
  void initState() {
    super.initState();
    _category = ConsentDataCategory.image;
    _purposeController = TextEditingController(text: _category.defaultPurpose);
    _costController = TextEditingController(text: '0');
  }

  @override
  void dispose() {
    _purposeController.dispose();
    _costController.dispose();
    super.dispose();
  }

  void _setCategory(ConsentDataCategory? value) {
    if (value == null) return;
    setState(() {
      _category = value;
      if (_purposeController.text.trim().isEmpty ||
          ConsentDataCategory.values.any((ConsentDataCategory item) =>
              item.defaultPurpose == _purposeController.text.trim())) {
        _purposeController.text = value.defaultPurpose;
      }
    });
  }

  void _submit() {
    if (!(_formKey.currentState?.validate() ?? false)) return;
    final double? cost = double.tryParse(_costController.text.trim());
    if (cost == null || !cost.isFinite || cost < 0 || cost > 1000000000) return;
    Navigator.of(context).pop(ConsentDraft(
      personaId: widget.personaId,
      providerId: widget.model.providerId,
      modelId: widget.model.id,
      dataCategory: _category,
      estimatedCost: cost,
      purpose: _purposeController.text,
      authorizationScope: _category.defaultScope,
    ));
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
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
              const Text('新增第三方处理授权',
                  style: TextStyle(fontSize: 22, fontWeight: FontWeight.w700)),
              const SizedBox(height: 6),
              Text('${widget.model.providerName} · ${widget.model.displayName}',
                  style: TextStyle(
                      color: Theme.of(context).colorScheme.onSurfaceVariant)),
              const SizedBox(height: 20),
              DropdownButtonFormField<ConsentDataCategory>(
                key: const Key('consent-category'),
                initialValue: _category,
                decoration: const InputDecoration(
                    labelText: '数据类别', border: OutlineInputBorder()),
                items: ConsentDataCategory.values
                    .map((ConsentDataCategory category) =>
                        DropdownMenuItem<ConsentDataCategory>(
                            value: category, child: Text(category.label)))
                    .toList(growable: false),
                onChanged: _setCategory,
              ),
              const SizedBox(height: 16),
              TextFormField(
                key: const Key('consent-purpose'),
                controller: _purposeController,
                maxLength: 512,
                decoration: const InputDecoration(
                    labelText: '处理用途', border: OutlineInputBorder()),
                validator: (String? value) =>
                    value == null || value.trim().isEmpty ? '请输入处理用途' : null,
              ),
              const SizedBox(height: 8),
              TextFormField(
                controller: _costController,
                keyboardType:
                    const TextInputType.numberWithOptions(decimal: true),
                decoration: const InputDecoration(
                    labelText: '预计费用上限',
                    prefixText: 'USD ',
                    border: OutlineInputBorder()),
                validator: (String? value) {
                  final double? cost = double.tryParse(value?.trim() ?? '');
                  return cost == null || !cost.isFinite || cost < 0
                      ? '请输入不小于 0 的数字'
                      : null;
                },
              ),
              const SizedBox(height: 8),
              Text('授权作用域：${_category.defaultScope}',
                  style: TextStyle(
                      color: Theme.of(context).colorScheme.onSurfaceVariant)),
              const SizedBox(height: 20),
              FilledButton.icon(
                key: const Key('consent-submit'),
                onPressed: _submit,
                icon: const Icon(Icons.verified_user_outlined),
                label: const Text('确认授权'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
