import 'package:flutter/material.dart';

import 'persona.dart';
import 'persona_controller.dart';
import '../imports/import_controller.dart';
import '../imports/import_file.dart';
import '../imports/import_job.dart';
import '../imports/import_upload_controller.dart';
import '../imports/import_workspace_screen.dart';
import '../imports/import_review_controller.dart';
import '../models/model_controller.dart';
import '../models/model_option.dart';
import '../models/model_selection_screen.dart';
import '../consents/consent_controller.dart';
import '../consents/consent_screen.dart';

class PersonaWorkspaceScreen extends StatefulWidget {
  const PersonaWorkspaceScreen(
      {required this.controller,
      this.importControllerFactory,
      this.importFileSource,
      this.importUploadControllerFactory,
      this.importReviewControllerFactory,
      this.modelSelectionControllerFactory,
      this.consentControllerFactory,
      super.key});

  final PersonaController controller;
  final ImportController Function(Persona persona)? importControllerFactory;
  final ImportFileSource? importFileSource;
  final ImportUploadController Function(Persona persona, ImportJob? job)?
      importUploadControllerFactory;
  final ImportReviewController Function(Persona persona, ImportJob job)?
      importReviewControllerFactory;
  final ModelSelectionController Function(ModelOption? selected)?
      modelSelectionControllerFactory;
  final ConsentController Function(Persona persona)? consentControllerFactory;

  @override
  State<PersonaWorkspaceScreen> createState() => _PersonaWorkspaceScreenState();
}

class _PersonaWorkspaceScreenState extends State<PersonaWorkspaceScreen> {
  ModelOption? _selectedModel;
  @override
  void initState() {
    super.initState();
    widget.controller.load();
  }

  Future<void> _openCreateForm() async {
    final PersonaDraft? draft = await showModalBottomSheet<PersonaDraft>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (BuildContext context) => const _PersonaFormSheet(),
    );
    if (!mounted || draft == null) return;
    await widget.controller.create(draft);
  }

  void _openImports(Persona persona) {
    final ImportController Function(Persona persona)? factory =
        widget.importControllerFactory;
    if (factory == null) return;
    Navigator.of(context).push(MaterialPageRoute<void>(
      builder: (BuildContext context) => ImportWorkspaceScreen(
        persona: persona,
        controller: factory(persona),
        fileSource: widget.importFileSource,
        uploadControllerFactory: widget.importUploadControllerFactory == null
            ? null
            : (ImportJob? job) =>
                widget.importUploadControllerFactory!(persona, job),
        reviewControllerFactory: widget.importReviewControllerFactory == null
            ? null
            : (ImportJob job) =>
                widget.importReviewControllerFactory!(persona, job),
      ),
    ));
  }

  Future<void> _openModelSelection() async {
    final ModelSelectionController Function(ModelOption? selected)? factory =
        widget.modelSelectionControllerFactory;
    if (factory == null) return;
    final ModelOption? selected = await Navigator.of(context).push<ModelOption>(
      MaterialPageRoute<ModelOption>(
        builder: (BuildContext context) => ModelSelectionScreen(
          controller: factory(_selectedModel),
          onSelected: (ModelOption model) => Navigator.of(context).pop(model),
        ),
      ),
    );
    if (mounted && selected != null) setState(() => _selectedModel = selected);
  }

  void _openConsents(Persona persona) {
    final ConsentController Function(Persona persona)? factory =
        widget.consentControllerFactory;
    if (factory == null) return;
    Navigator.of(context).push(MaterialPageRoute<void>(
      builder: (BuildContext context) => ConsentScreen(
        personaName: persona.displayName,
        controller: factory(persona),
        selectedModel: _selectedModel,
      ),
    ));
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: widget.controller,
      builder: (BuildContext context, Widget? child) {
        final bool loading = widget.controller.state == PersonaState.loading;
        final bool saving = widget.controller.state == PersonaState.saving;
        return Scaffold(
          appBar: AppBar(
            title: const Text('人物'),
            actions: <Widget>[
              if (widget.modelSelectionControllerFactory != null)
                IconButton(
                  tooltip: '选择模型',
                  onPressed: _openModelSelection,
                  icon: const Icon(Icons.tune_rounded),
                ),
              IconButton(
                tooltip: '刷新人物',
                onPressed: loading || saving ? null : widget.controller.load,
                icon: const Icon(Icons.refresh_rounded),
              ),
            ],
          ),
          body: SafeArea(
            child: loading && widget.controller.personas.isEmpty
                ? const Center(child: CircularProgressIndicator())
                : RefreshIndicator(
                    onRefresh: widget.controller.load,
                    child: widget.controller.personas.isEmpty
                        ? _EmptyPersonaState(
                            errorMessage: widget.controller.errorMessage,
                            onCreate: _openCreateForm,
                            onRetry: widget.controller.load,
                          )
                        : ListView(
                            padding: const EdgeInsets.fromLTRB(16, 12, 16, 32),
                            children: <Widget>[
                              const Text('选择一个人物开始准备专属对话',
                                  style: TextStyle(
                                      fontSize: 16,
                                      fontWeight: FontWeight.w600)),
                              const SizedBox(height: 4),
                              Text('人物身份会用于后续导入和对话设置。',
                                  style: TextStyle(
                                      color: Theme.of(context)
                                          .colorScheme
                                          .onSurfaceVariant)),
                              const SizedBox(height: 16),
                              if (_selectedModel != null) ...<Widget>[
                                ListTile(
                                  contentPadding: EdgeInsets.zero,
                                  leading:
                                      const Icon(Icons.auto_awesome_rounded),
                                  title: const Text('当前模型'),
                                  subtitle: Text(
                                      '${_selectedModel!.providerName} · ${_selectedModel!.displayName}'),
                                  trailing:
                                      const Icon(Icons.chevron_right_rounded),
                                  onTap: _openModelSelection,
                                ),
                                const SizedBox(height: 8),
                              ],
                              ...widget.controller.personas.map(
                                  (Persona persona) => _PersonaCard(
                                      persona: persona,
                                      onTap:
                                          widget.importControllerFactory == null
                                              ? null
                                              : () => _openImports(persona),
                                      onConsent:
                                          widget.consentControllerFactory ==
                                                  null
                                              ? null
                                              : () => _openConsents(persona))),
                              const SizedBox(height: 8),
                              OutlinedButton.icon(
                                onPressed: saving ? null : _openCreateForm,
                                icon:
                                    const Icon(Icons.person_add_alt_1_rounded),
                                label: const Text('创建另一个人物'),
                              ),
                            ],
                          ),
                  ),
          ),
          floatingActionButton: widget.controller.personas.isNotEmpty
              ? FloatingActionButton.extended(
                  onPressed: saving ? null : _openCreateForm,
                  icon: const Icon(Icons.add_rounded),
                  label: const Text('创建人物'),
                )
              : null,
        );
      },
    );
  }
}

class _EmptyPersonaState extends StatelessWidget {
  const _EmptyPersonaState(
      {required this.errorMessage,
      required this.onCreate,
      required this.onRetry});

  final String? errorMessage;
  final VoidCallback onCreate;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final bool hasError = errorMessage != null;
    return ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.all(24),
      children: <Widget>[
        const SizedBox(height: 64),
        Icon(hasError ? Icons.cloud_off_rounded : Icons.people_alt_outlined,
            size: 56, color: Theme.of(context).colorScheme.primary),
        const SizedBox(height: 16),
        Text(hasError ? '人物列表暂时不可用' : '还没有人物',
            textAlign: TextAlign.center,
            style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w700)),
        const SizedBox(height: 8),
        Text(
          hasError ? errorMessage! : '先创建一个人物，再为 TA 导入聊天资料和设置身份。',
          textAlign: TextAlign.center,
          style: TextStyle(
              color: Theme.of(context).colorScheme.onSurfaceVariant,
              height: 1.4),
        ),
        const SizedBox(height: 24),
        FilledButton.icon(
          onPressed: hasError ? onRetry : onCreate,
          icon: Icon(hasError
              ? Icons.refresh_rounded
              : Icons.person_add_alt_1_rounded),
          label: Text(hasError ? '重试加载' : '创建人物'),
        ),
      ],
    );
  }
}

class _PersonaCard extends StatelessWidget {
  const _PersonaCard({required this.persona, this.onTap, this.onConsent});

  final Persona persona;
  final VoidCallback? onTap;
  final VoidCallback? onConsent;

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        leading: CircleAvatar(child: Text(persona.displayName.substring(0, 1))),
        title: Text(persona.displayName,
            style: const TextStyle(fontWeight: FontWeight.w700)),
        subtitle: Text(persona.relationshipLabel),
        trailing: Row(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            if (onConsent != null)
              IconButton(
                key: Key('consent-open-${persona.id}'),
                tooltip: '管理授权',
                onPressed: onConsent,
                icon: const Icon(Icons.shield_outlined),
              ),
            const Icon(Icons.chevron_right_rounded),
          ],
        ),
        onTap: onTap,
      ),
    );
  }
}

class _PersonaFormSheet extends StatefulWidget {
  const _PersonaFormSheet();

  @override
  State<_PersonaFormSheet> createState() => _PersonaFormSheetState();
}

class _PersonaFormSheetState extends State<_PersonaFormSheet> {
  final GlobalKey<FormState> _formKey = GlobalKey<FormState>();
  final TextEditingController _nameController = TextEditingController();
  final TextEditingController _customLabelController = TextEditingController();
  final TextEditingController _descriptionController = TextEditingController();
  PersonaRelationship _relationship = PersonaRelationship.friend;

  @override
  void dispose() {
    _nameController.dispose();
    _customLabelController.dispose();
    _descriptionController.dispose();
    super.dispose();
  }

  void _submit() {
    if (!(_formKey.currentState?.validate() ?? false)) return;
    Navigator.of(context).pop(
      PersonaDraft(
        displayName: _nameController.text,
        relationshipType: _relationship,
        customLabel: _relationship == PersonaRelationship.custom
            ? _customLabelController.text
            : null,
        relationshipDescription: _descriptionController.text,
      ),
    );
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
              const Text('创建人物',
                  style: TextStyle(fontSize: 22, fontWeight: FontWeight.w700)),
              const SizedBox(height: 4),
              Text('先选择身份设定，后续导入时可以继续调整。',
                  style: TextStyle(
                      color: Theme.of(context).colorScheme.onSurfaceVariant)),
              const SizedBox(height: 20),
              TextFormField(
                key: const Key('persona-display-name'),
                controller: _nameController,
                autofocus: true,
                textInputAction: TextInputAction.next,
                decoration: const InputDecoration(
                    labelText: '人物名称',
                    hintText: '例如：妈妈、小雅',
                    border: OutlineInputBorder()),
                validator: (String? value) =>
                    value == null || value.trim().isEmpty ? '请输入人物名称' : null,
              ),
              const SizedBox(height: 16),
              const Text('关系身份', style: TextStyle(fontWeight: FontWeight.w600)),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: PersonaRelationship.values
                    .map((PersonaRelationship relationship) {
                  return ChoiceChip(
                    label: Text(relationship.label),
                    selected: relationship == _relationship,
                    onSelected: (_) =>
                        setState(() => _relationship = relationship),
                  );
                }).toList(),
              ),
              if (_relationship == PersonaRelationship.custom) ...<Widget>[
                const SizedBox(height: 16),
                TextFormField(
                  controller: _customLabelController,
                  decoration: const InputDecoration(
                      labelText: '自定义关系',
                      hintText: '例如：旧友、老师',
                      border: OutlineInputBorder()),
                  validator: (String? value) =>
                      _relationship == PersonaRelationship.custom &&
                              (value == null || value.trim().isEmpty)
                          ? '请输入自定义关系'
                          : null,
                ),
              ],
              const SizedBox(height: 16),
              TextFormField(
                controller: _descriptionController,
                maxLines: 3,
                decoration: const InputDecoration(
                    labelText: '关系描述（可选）',
                    hintText: '记录称呼、语气或相处特点',
                    border: OutlineInputBorder()),
              ),
              const SizedBox(height: 20),
              SizedBox(
                height: 48,
                child: FilledButton.icon(
                    onPressed: _submit,
                    icon: const Icon(Icons.check_rounded),
                    label: const Text('保存人物')),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
