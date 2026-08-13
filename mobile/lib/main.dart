import 'package:flutter/material.dart';

import 'app/app_dependencies.dart';
import 'app/past_partner_app.dart';

void main() {
  final AppDependencies dependencies = AppDependencies();
  runApp(PastPartnerApp(
    sessionController: dependencies.sessionController,
    appearanceController: dependencies.appearanceController,
    personaController: dependencies.personaController,
  ));
}
