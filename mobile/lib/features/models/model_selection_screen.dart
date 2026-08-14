import 'package:flutter/material.dart';

import 'model_controller.dart';
import 'model_option.dart';

class ModelSelectionScreen extends StatefulWidget {
  const ModelSelectionScreen({
    required this.controller,
    this.onSelected,
    super.key,
  });

  final ModelSelectionController controller;
  final ValueChanged<ModelOption>? onSelected;

  @override
  State<ModelSelectionScreen> createState() => _ModelSelectionScreenState();
}

class _ModelSelectionScreenState extends State<ModelSelectionScreen> {
  final TextEditingController _inputTokens =
      TextEditingController(text: '100000');
  final TextEditingController _outputTokens =
      TextEditingController(text: '20000');

  @override
  void initState() {
    super.initState();
    widget.controller.load();
  }

  @override
  void dispose() {
    _inputTokens.dispose();
    _outputTokens.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: widget.controller,
      builder: (BuildContext context, Widget? child) {
        final ModelSelectionController controller = widget.controller;
        return Scaffold(
          appBar: AppBar(
            title: const Text('选择模型'),
            actions: <Widget>[
              IconButton(
                tooltip: '刷新模型目录',
                onPressed: controller.state == ModelSelectionState.loading
                    ? null
                    : controller.load,
                icon: const Icon(Icons.refresh_rounded),
              ),
            ],
          ),
          body: SafeArea(child: _body(context, controller)),
        );
      },
    );
  }

  Widget _body(BuildContext context, ModelSelectionController controller) {
    if (controller.state == ModelSelectionState.loading &&
        controller.models.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }
    if (controller.state == ModelSelectionState.error &&
        controller.models.isEmpty) {
      return _ErrorState(
        message: controller.errorMessage ?? '模型目录加载失败，请重试。',
        onRetry: controller.load,
      );
    }
    if (controller.models.isEmpty) {
      return _ErrorState(
        message: '服务端暂无可用模型。',
        onRetry: controller.load,
      );
    }
    return RefreshIndicator(
      onRefresh: controller.load,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 32),
        children: <Widget>[
          if (controller.state == ModelSelectionState.error &&
              controller.errorMessage != null)
            _InlineRetry(
              message: controller.errorMessage!,
              onRetry: controller.load,
            ),
          Text('按供应商比较能力与价格', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 10),
          _ProviderFilters(controller: controller),
          const SizedBox(height: 12),
          ...controller.visibleModels.map((ModelOption model) =>
              _ModelCard(controller: controller, model: model)),
          if (controller.visibleModels.isEmpty)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 32),
              child: Center(child: Text('该供应商暂无模型。')),
            ),
          const SizedBox(height: 8),
          _EstimatePanel(
            controller: controller,
            inputTokens: _inputTokens,
            outputTokens: _outputTokens,
          ),
          if (controller.selected != null &&
              widget.onSelected != null) ...<Widget>[
            const SizedBox(height: 12),
            SizedBox(
              width: double.infinity,
              height: 48,
              child: FilledButton.icon(
                key: const Key('model-confirm-selection'),
                onPressed: () => widget.onSelected!(controller.selected!),
                icon: const Icon(Icons.check_rounded),
                label: const Text('确认使用此模型'),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _ProviderFilters extends StatelessWidget {
  const _ProviderFilters({required this.controller});

  final ModelSelectionController controller;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: '模型供应商筛选',
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(
          children: <Widget>[
            ChoiceChip(
              label: const Text('全部'),
              selected: controller.providerFilter == null,
              onSelected: (_) => controller.setProviderFilter(null),
            ),
            const SizedBox(width: 8),
            ...controller.providers.map((String providerId) => Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: ChoiceChip(
                    label: Text(_providerName(providerId)),
                    selected: controller.providerFilter == providerId,
                    onSelected: (_) => controller.setProviderFilter(providerId),
                  ),
                )),
          ],
        ),
      ),
    );
  }
}

class _ModelCard extends StatelessWidget {
  const _ModelCard({required this.controller, required this.model});

  final ModelSelectionController controller;
  final ModelOption model;

  @override
  Widget build(BuildContext context) {
    final bool selected = controller.selected?.providerId == model.providerId &&
        controller.selected?.id == model.id;
    final String description = '${model.providerName}，${model.contextLabel}，'
        '${selected ? '已选中' : '未选中'}';
    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: Semantics(
        container: true,
        label: '$description，${model.displayName}',
        child: InkWell(
          key: Key('model-select-${model.providerId}-${model.id}'),
          borderRadius: BorderRadius.circular(12),
          onTap: () => controller.select(model),
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: <Widget>[
                          Text(model.displayName,
                              style: const TextStyle(
                                  fontWeight: FontWeight.w700, fontSize: 16)),
                          const SizedBox(height: 3),
                          Text(model.providerName,
                              style: TextStyle(
                                  color: Theme.of(context)
                                      .colorScheme
                                      .onSurfaceVariant)),
                        ],
                      ),
                    ),
                    Icon(
                      selected
                          ? Icons.check_circle_rounded
                          : Icons.radio_button_unchecked_rounded,
                      color: selected
                          ? Theme.of(context).colorScheme.primary
                          : Theme.of(context).colorScheme.outline,
                      size: 28,
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: <Widget>[
                    _InfoChip(label: model.contextLabel),
                    ...model.capabilities.map((String value) =>
                        _InfoChip(label: _capabilityLabel(value))),
                  ],
                ),
                const SizedBox(height: 10),
                Text(
                  model.pricing.hasTokenPricing
                      ? '输入 ${_price(model.pricing.inputPricePerMillionTokens!)} / 输出 ${_price(model.pricing.outputPricePerMillionTokens!)} ${model.pricing.currency} / 1M tokens'
                      : '价格未配置，暂不能估算',
                  style: TextStyle(
                    color: model.pricing.hasTokenPricing
                        ? Theme.of(context).colorScheme.onSurface
                        : Theme.of(context).colorScheme.error,
                  ),
                ),
                const SizedBox(height: 4),
                Text(model.refreshedAtLabel,
                    style: TextStyle(
                        color: Theme.of(context).colorScheme.onSurfaceVariant,
                        fontSize: 12)),
                if (model.privacyMetadata.isNotEmpty) ...<Widget>[
                  const SizedBox(height: 4),
                  Text('隐私：${model.privacyMetadata.join('、')}',
                      style: TextStyle(
                          color: Theme.of(context).colorScheme.onSurfaceVariant,
                          fontSize: 12)),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _EstimatePanel extends StatelessWidget {
  const _EstimatePanel({
    required this.controller,
    required this.inputTokens,
    required this.outputTokens,
  });

  final ModelSelectionController controller;
  final TextEditingController inputTokens;
  final TextEditingController outputTokens;

  @override
  Widget build(BuildContext context) {
    final bool estimating = controller.state == ModelSelectionState.estimating;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text('成本预估', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 4),
            Text('按本次对话预计的输入和输出 token 计算，价格仅供参考。',
                style: TextStyle(
                    color: Theme.of(context).colorScheme.onSurfaceVariant)),
            const SizedBox(height: 12),
            Row(
              children: <Widget>[
                Expanded(
                  child: TextField(
                    key: const Key('model-input-tokens'),
                    controller: inputTokens,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(
                        labelText: '输入 token', border: OutlineInputBorder()),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: TextField(
                    key: const Key('model-output-tokens'),
                    controller: outputTokens,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(
                        labelText: '输出 token', border: OutlineInputBorder()),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            SizedBox(
              width: double.infinity,
              height: 48,
              child: FilledButton.icon(
                onPressed: estimating
                    ? null
                    : () => controller.estimate(
                          inputTokens: int.tryParse(inputTokens.text) ?? -1,
                          outputTokens: int.tryParse(outputTokens.text) ?? -1,
                        ),
                icon: const Icon(Icons.calculate_outlined),
                label: Text(estimating ? '计算中...' : '估算当前模型'),
              ),
            ),
            if (controller.estimateErrorMessage != null) ...<Widget>[
              const SizedBox(height: 10),
              Text(controller.estimateErrorMessage!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error)),
            ],
            if (controller.estimateResult != null) ...<Widget>[
              const SizedBox(height: 10),
              Text(
                  '预计费用：${_price(controller.estimateResult!.estimatedCost)} ${controller.estimateResult!.currency}',
                  style: const TextStyle(fontWeight: FontWeight.w700)),
            ],
          ],
        ),
      ),
    );
  }
}

class _InfoChip extends StatelessWidget {
  const _InfoChip({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) => Chip(
        label: Text(label),
        visualDensity: VisualDensity.compact,
      );
}

class _ErrorState extends StatelessWidget {
  const _ErrorState({required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              Icon(Icons.cloud_off_rounded,
                  size: 52, color: Theme.of(context).colorScheme.primary),
              const SizedBox(height: 14),
              Text(message, textAlign: TextAlign.center),
              const SizedBox(height: 16),
              FilledButton.icon(
                  onPressed: onRetry,
                  icon: const Icon(Icons.refresh_rounded),
                  label: const Text('重试')),
            ],
          ),
        ),
      );
}

class _InlineRetry extends StatelessWidget {
  const _InlineRetry({required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.fromLTRB(12, 8, 8, 8),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.errorContainer,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Text(message,
                style: TextStyle(
                    color: Theme.of(context).colorScheme.onErrorContainer)),
          ),
          IconButton(
            tooltip: '重试加载模型目录',
            onPressed: onRetry,
            icon: const Icon(Icons.refresh_rounded),
          ),
        ],
      ),
    );
  }
}

String _providerName(String providerId) => switch (providerId) {
      'deepseek' => 'DeepSeek',
      'xiaomi_mimo' => 'Xiaomi MiMo',
      'qwen' => 'Alibaba Qwen',
      'openai' => 'OpenAI',
      'anthropic' => 'Anthropic',
      'gemini' => 'Google Gemini',
      'ollama' => 'Ollama',
      'custom_openai' => '自定义 OpenAI',
      'custom_http' => '自定义 HTTP',
      _ => providerId,
    };

String _capabilityLabel(String value) => switch (value) {
      'chat' => '对话',
      'streaming' => '流式',
      'vision' => '视觉',
      'audio' => '音频',
      'embedding' => 'Embedding',
      'fine_tuning' => '微调',
      _ => value,
    };

String _price(double value) => value
    .toStringAsFixed(4)
    .replaceFirst(RegExp(r'0+$'), '')
    .replaceFirst(RegExp(r'\.$'), '');
