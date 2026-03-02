aibox introduces an automated hand guidance solution that enables grasping of the target objects using tactile bracelet

Two key components of automated hand navigation system are:
1) TaskController class (located in controller.py) - provides basic functionality of using automated hand navigation logic algorithm for a live video stream (by default collected from external camera). Default behaviour is defined by method 'experimental_loop' that, for each subsequent frame, based on output from object detectors and, optionally, object tracker and depth estimator provides guiding signal to the bracelet (i.e., information about position of the hand, target object and their surroundings is translated to vibration commands guiding hand in the specific direction).
2) BraceletController class (located in bracelet.py) - provides implementation of all the functions required for automated hand navigation for a single frame (utilized in TaskController). Default behaviour is defined in the method 'navigate_hand' that processes information about hand and target bounding boxes and based on their relative positions provide guiding signal in specific direction or informs user that object is in front of the hand and can be grasped.

To use Android smartphone as a control device:
1) Set up the Android Studio on your local machine (https://developer.android.com/studio)
2) Open the android_client as a project in your Android Studio
3) Open the repository with the whole project in you IDE (e.g., Visual Studio Code)
4) Get your bracelet and belt IDs using helper functions from belt_bracelet_integration, fill them in the MainActivity.kt and BleManager.kt to enable Bluetooth connection with devices through the app (skip if you want to test without the belt/bracelet)
5) Add .env file in the auditory_interface, put there OPENAI_API_KEY=your_API_key (Note: currently, the system by default connects to the OpenAI API to utilize ChatGPT as the backbone of the MCP server. If you want to use different provider, adapt scripts accordingly)
6) Connect your smartphone to the Android Studio (https://developer.android.com/develop/connectivity/wifi)
7) Run auditory_interace/server_main on your IDE
8) Run the app on the Android Studio

If everything worked properly, you should see the live feed from your smarphone camera on your computer, and once you say wake word ('Hans' by default) the smartphone will transcribe your request and send it to the MCP server for further processing.