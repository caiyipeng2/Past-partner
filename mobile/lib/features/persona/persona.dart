enum PersonaRelationship {
  father('father', '父亲'),
  mother('mother', '母亲'),
  relative('relative', '亲人'),
  friend('friend', '朋友'),
  partner('partner', '情侣'),
  custom('custom', '自定义');

  const PersonaRelationship(this.value, this.label);

  final String value;
  final String label;

  static PersonaRelationship fromValue(String value) =>
      PersonaRelationship.values.firstWhere(
        (PersonaRelationship item) => item.value == value,
        orElse: () => PersonaRelationship.custom,
      );
}

class Persona {
  const Persona({
    required this.id,
    required this.displayName,
    required this.relationshipType,
    this.customLabel,
    this.relationshipDescription,
  });

  final String id;
  final String displayName;
  final PersonaRelationship relationshipType;
  final String? customLabel;
  final String? relationshipDescription;

  String get relationshipLabel => relationshipType == PersonaRelationship.custom
      ? (customLabel ?? PersonaRelationship.custom.label)
      : relationshipType.label;

  factory Persona.fromJson(Map<String, dynamic> json) {
    final dynamic id = json['id'];
    final dynamic displayName = json['display_name'];
    final dynamic relationshipType = json['relationship_type'];
    if (id is! String ||
        id.isEmpty ||
        displayName is! String ||
        displayName.isEmpty ||
        relationshipType is! String) {
      throw const FormatException('The persona response is invalid.');
    }
    final dynamic customLabel =
        json['custom_label'] ?? json['relationship_label'];
    final dynamic description = json['relationship_description'];
    return Persona(
      id: id,
      displayName: displayName,
      relationshipType: PersonaRelationship.fromValue(relationshipType),
      customLabel:
          customLabel is String && customLabel.isNotEmpty ? customLabel : null,
      relationshipDescription:
          description is String && description.isNotEmpty ? description : null,
    );
  }
}

class PersonaDraft {
  const PersonaDraft({
    required this.displayName,
    required this.relationshipType,
    this.customLabel,
    this.relationshipDescription,
  });

  final String displayName;
  final PersonaRelationship relationshipType;
  final String? customLabel;
  final String? relationshipDescription;

  Map<String, dynamic> toJson() {
    final Map<String, dynamic> payload = <String, dynamic>{
      'display_name': displayName.trim(),
      'relationship_type': relationshipType.value,
    };
    if (customLabel != null && customLabel!.trim().isNotEmpty) {
      payload['custom_label'] = customLabel!.trim();
    }
    if (relationshipDescription != null &&
        relationshipDescription!.trim().isNotEmpty) {
      payload['relationship_description'] = relationshipDescription!.trim();
    }
    return payload;
  }
}
