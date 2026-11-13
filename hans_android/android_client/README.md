# Newest updates

This page summarizes the Android experiment setup, trial logging, and experimenter terminal controls on the `feat/phone_auditory_recovery` branch.

## Files added

| File | Purpose |
|---|---|
| [experiment-controls.ps1](https://github.com/BwanaKumbwa/tactile-guidance/blob/feat/phone_auditory_recovery/hans_android/android_client/experiment-controls.ps1 "experiment-controls.ps1") | Defines the short PowerShell commands `s`, `r`, `t`, `o`, `y`, `n`, `f`, and `c`. Each sends the existing experiment-command ADB broadcast to the Android app. It defaults to the ARCore app package and supports overriding it with `HANS_PACKAGE`. |
| [ExperimentSession.kt](https://github.com/BwanaKumbwa/tactile-guidance/blob/feat/phone_auditory_recovery/hans_android/android_client/app/src/main/java/com/example/hans/ExperimentSession.kt "ExperimentSession.kt") | Stores the selected app mode, Participant ID, and Condition in app-private `SharedPreferences`, and exposes the current session to other Android components. |
| [ModeSelectionActivity.kt](https://github.com/BwanaKumbwa/tactile-guidance/blob/feat/phone_auditory_recovery/hans_android/android_client/app/src/main/java/com/example/hans/ModeSelectionActivity.kt "ModeSelectionActivity.kt") | Shows the initial **Experiment** and **Usage** choices. Usage opens the existing app directly; Experiment opens setup. |
| [activity_mode_selection.xml](https://github.com/BwanaKumbwa/tactile-guidance/blob/feat/phone_auditory_recovery/hans_android/android_client/app/src/main/res/layout/activity_mode_selection.xml "activity_mode_selection.xml") | Defines the mode-selection screen layout. |
| [ExperimentSetupActivity.kt](https://github.com/BwanaKumbwa/tactile-guidance/blob/feat/phone_auditory_recovery/hans_android/android_client/app/src/main/java/com/example/hans/ExperimentSetupActivity.kt "ExperimentSetupActivity.kt") | Validates Participant ID and Condition selection, saves them to `ExperimentSession`, then opens the existing main app. |
| [activity_experiment_setup.xml](https://github.com/BwanaKumbwa/tactile-guidance/blob/feat/phone_auditory_recovery/hans_android/android_client/app/src/main/res/layout/activity_experiment_setup.xml "activity_experiment_setup.xml") | Defines the experiment setup form with Participant ID, Conditions 0–2, and **Start Experiment**. |
| [ExperimentTrialLogger.kt](https://github.com/BwanaKumbwa/tactile-guidance/blob/feat/phone_auditory_recovery/hans_android/android_client/app/src/main/java/com/example/hans/ExperimentTrialLogger.kt "ExperimentTrialLogger.kt") | Records trial start/end, recovery success and its latest timestamp, wrong-target count, overshoot count, target, outcome, Participant ID, and Condition. It persists changes after each event and writes CSV rows with descriptive headers. |
| [ExperimentCommandReceiver.kt](https://github.com/BwanaKumbwa/tactile-guidance/blob/feat/phone_auditory_recovery/hans_android/android_client/app/src/main/java/com/example/hans/ExperimentCommandReceiver.kt "ExperimentCommandReceiver.kt") | Receives the ADB broadcasts, passes commands to the logger, and closes the app for `c`. |
| [ExperimentCommandControls.kt](https://github.com/BwanaKumbwa/tactile-guidance/blob/feat/phone_auditory_recovery/hans_android/android_client/app/src/main/java/com/example/hans/ExperimentCommandControls.kt "ExperimentCommandControls.kt") | Registers the command receiver only for an active Experiment session and prints the ADB command format in Logcat. |

## Existing files updated

| File | Update |
|---|---|
| [AndroidManifest.xml](https://github.com/BwanaKumbwa/tactile-guidance/blob/feat/phone_auditory_recovery/hans_android/android_client/app/src/main/AndroidManifest.xml "AndroidManifest.xml") | Makes mode selection the launcher and registers the setup screen and existing main activity. |
| [strings.xml](https://github.com/BwanaKumbwa/tactile-guidance/blob/feat/phone_auditory_recovery/hans_android/android_client/app/src/main/res/values/strings.xml "strings.xml") | Adds labels and validation messages for the setup flow. |
| [withArcore MainActivity.kt](https://github.com/BwanaKumbwa/tactile-guidance/blob/feat/phone_auditory_recovery/hans_android/android_client/app/src/withArcore/kotlin/com/example/hans/MainActivity.kt "withArcore MainActivity.kt") | Starts and stops experiment-command listening with the activity lifecycle and supplies the current tracked target to trial logging. |
| [noArcore MainActivity.kt](https://github.com/BwanaKumbwa/tactile-guidance/blob/feat/phone_auditory_recovery/hans_android/android_client/app/src/noArcore/kotlin/com/example/hans/MainActivity.kt "noArcore MainActivity.kt") | Starts and stops experiment-command listening with the activity lifecycle. |

## Running the experiment controls

In Android Studio's PowerShell terminal, from the repository root, dot-source the helper:

```powershell
. .\hans_android\android_client\experiment-controls.ps1
```

With the app running in Experiment mode, type `s`, `r`, `t`, `o`, `y`, `n`, `f`, or `c` to send the matching command. The helper temporarily replaces PowerShell's built-in `r` (`Invoke-History`) alias; run `Remove-HansExperimentControls` to remove the shortcuts and restore the alias.

The CSV is written to the app's private `files/experiment_logs/` directory, with a filename based on Participant ID and Condition. Usage mode bypasses the experiment setup and does not create experiment trial logs.
