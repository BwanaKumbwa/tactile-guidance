package com.example.hans

import android.content.Intent
import android.os.Bundle
import android.util.Log
import android.widget.ImageView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.bottomnavigation.BottomNavigationView
import android.content.SharedPreferences
import androidx.constraintlayout.widget.ConstraintLayout
import android.view.ViewGroup
import androidx.core.content.ContextCompat

class BluetoothActivity : AppCompatActivity() {

    // BLE MAC Addresses
    private val MAC_BRACELET = BuildConfig.MAC_BRACELET
    private val MAC_BELT     = BuildConfig.MAC_BELT

    // Use singleton instead of local instances
    private lateinit var braceletManager: BleManager
    private lateinit var beltManager: BleManager

    private lateinit var bottomNav: BottomNavigationView
    private lateinit var connectBelt: ConstraintLayout
    private lateinit var connectBracelet: ConstraintLayout
    private lateinit var beltBluetooth: ImageView
    private lateinit var braceletBluetooth: ImageView
    private lateinit var beltIcon: ImageView
    private lateinit var braceletIcon: ImageView
    private lateinit var prefs: SharedPreferences
    private var isConnecting = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_bluetooth)

        // Get singleton instances
        braceletManager = BleManagerSingleton.getBraceletManager(applicationContext)
        beltManager = BleManagerSingleton.getBeltManager(applicationContext)

        // PREFS
        prefs = getSharedPreferences("ble_state", MODE_PRIVATE)

        // UI
        bottomNav = findViewById(R.id.bottomNavigation)
        connectBelt = findViewById(R.id.button_belt)
        connectBracelet = findViewById(R.id.button_bracelet)

        beltIcon = findViewById(R.id.icon_belt)
        braceletIcon = findViewById(R.id.icon_bracelet)

        beltBluetooth = findViewById(R.id.iconLeftBelt)
        braceletBluetooth = findViewById(R.id.iconLeftBracelet)

        // SHOW SAVED STATE
        renderSavedState()

        // BELT BUTTON
        connectBelt.setOnClickListener {
            if (isConnecting) {
                return@setOnClickListener
            }

            if (beltManager.isConnected()) {

                Toast.makeText(
                    this,
                    "Belt already connected",
                    Toast.LENGTH_SHORT
                ).show()

                saveState()
                updateUIFromRealState()
                return@setOnClickListener
            }

            isConnecting = true
            connectBelt.isEnabled = false

            Toast.makeText(
                this,
                "Connecting belt",
                Toast.LENGTH_SHORT
            ).show()

            connectBelt()
        }

        // BRACELET BUTTON
        connectBracelet.setOnClickListener {
            if (isConnecting) {
                return@setOnClickListener
            }

            if (braceletManager.isConnected()) {

                Toast.makeText(
                    this,
                    "Bracelet already connected",
                    Toast.LENGTH_SHORT
                ).show()

                saveState()
                updateUIFromRealState()
                return@setOnClickListener
            }

            isConnecting = true
            connectBracelet.isEnabled = false

            Toast.makeText(
                this,
                "Connecting bracelet",
                Toast.LENGTH_SHORT
            ).show()

            connectBracelet()
        }

        // BOTTOM NAVIGATION
        bottomNav.selectedItemId = R.id.menu_home
        bottomNav.post {updateBottomNavBackground(R.id.menu_home) }
        bottomNav.setOnItemSelectedListener { item ->

            if (isConnecting) {
                Toast.makeText(
                    this,
                    "Please wait, Bluetooth is connecting",
                    Toast.LENGTH_SHORT
                ).show()
                return@setOnItemSelectedListener false
            }

            when (item.itemId) {
                R.id.menu_home -> {
                    updateBottomNavBackground(R.id.menu_home)
                    true
                }

                R.id.menu_camera -> {
                    startActivity(Intent(this, MainActivity::class.java))
                    finish()
                    true
                }

                R.id.menu_setting -> {
                    startActivity(Intent(this, SettingsActivity::class.java))
                    finish()
                    true
                }

                else -> false
            }
        }
    }

    // CONNECT BELT
    private fun connectBelt() {

        Thread {
            try {
                beltManager.connect(MAC_BELT)
                Thread.sleep(1500)

                val connected = beltManager.isConnected()
                Log.d("HANS", "Belt connected = $connected")

                runOnUiThread {
                    isConnecting = false
                    connectBelt.isEnabled = true
                    saveState()
                    updateUIFromRealState()

                    if (connected) {
                        startActivity(
                            Intent(this, BluetoothConnectedActivity::class.java).apply {
                                putExtra("device_type", "belt")
                            }
                        )
                        finish()

                    } else {
                        startActivity(
                            Intent(this, BluetoothNotConnectedActivity::class.java).apply {
                                putExtra("device_type", "belt")
                            }
                        )
                        finish()
                    }
                }

            } catch (e: Exception) {
                Log.e("HANS", "Belt connection error", e)
                runOnUiThread {
                    isConnecting = false
                    connectBelt.isEnabled = true
                    updateUIFromRealState()
                }
            }
        }.start()
    }

    // CONNECT BRACELET
    private fun connectBracelet() {
        Thread {
            try {
                braceletManager.connect(MAC_BRACELET)
                Thread.sleep(1500)

                val connected = braceletManager.isConnected()
                Log.d("HANS", "Bracelet connected = $connected")

                runOnUiThread {
                    isConnecting = false
                    connectBracelet.isEnabled = true
                    saveState()
                    updateUIFromRealState()

                    if (connected) {
                        startActivity(
                            Intent(this, BluetoothConnectedActivity::class.java).apply {
                                putExtra("device_type", "bracelet")
                            }
                        )
                        finish()

                    } else {
                        startActivity(
                            Intent(
                                this, BluetoothNotConnectedActivity::class.java).apply {
                                putExtra("device_type", "bracelet")
                            }
                        )
                        finish()
                    }
                }

            } catch (e: Exception) {
                Log.e("HANS", "Bracelet connection error", e)
                runOnUiThread {
                    isConnecting = false
                    connectBracelet.isEnabled = true
                    updateUIFromRealState()
                }
            }
        }.start()
    }

    // SAVE STATE
    private fun saveState() {
        val beltConnected = beltManager.isConnected()
        val braceletConnected = braceletManager.isConnected()

        prefs.edit()
            .putBoolean("belt", beltConnected)
            .putBoolean("bracelet", braceletConnected)
            .apply()
    }

    // REAL STATE
    private fun updateUIFromRealState() {
        updateIconFromState(
            beltManager.isConnected(),
            braceletManager.isConnected()
        )
    }

    // ICON
    private fun updateIconFromState(
        beltConnected: Boolean,
        braceletConnected: Boolean
    ) {

        // BELT
        if (beltConnected) {
            connectBelt.setBackgroundResource(R.drawable.bg_green)
            beltIcon.setImageResource(R.drawable.check)
            beltBluetooth.setImageResource(R.drawable.connected)
        } else {
            connectBelt.setBackgroundResource(R.drawable.bg_red)
            beltIcon.setImageResource(R.drawable.cross)
            beltBluetooth.setImageResource(R.drawable.disconnected)
        }

        // BRACELET
        if (braceletConnected) {
            connectBracelet.setBackgroundResource(R.drawable.bg_green)
            braceletIcon.setImageResource(R.drawable.check)
            braceletBluetooth.setImageResource(R.drawable.connected)
        } else {
            connectBracelet.setBackgroundResource(R.drawable.bg_red)
            braceletIcon.setImageResource(R.drawable.cross)
            braceletBluetooth.setImageResource(R.drawable.disconnected)
        }
    }

    // RESTORE UI
    private fun renderSavedState() {
        val belt = prefs.getBoolean("belt", false)
        val bracelet = prefs.getBoolean("bracelet", false)

        updateIconFromState(belt, bracelet)
    }

    // RESUME
    override fun onResume() {
        super.onResume()
        renderSavedState()

        if (
            beltManager.isConnected() ||
            braceletManager.isConnected()
        ) {
            saveState()
            updateUIFromRealState()
        }
    }

    // DESTROY

    override fun onDestroy() {
        super.onDestroy()
    }

    private fun updateBottomNavBackground(page: Int) {

        val menuView = bottomNav.getChildAt(0) as ViewGroup

        val home = menuView.getChildAt(0)
        val camera = menuView.getChildAt(1)
        val setting = menuView.getChildAt(2)

        val rectangle = ContextCompat.getDrawable(this, R.drawable.bottom_rectangle)
        val rectangleSelected = ContextCompat.getDrawable(this, R.drawable.bottom_rectangle_selected)
        val topLeft = ContextCompat.getDrawable(this, R.drawable.bottom_top_left)
        val topLeftSelected = ContextCompat.getDrawable(this, R.drawable.bottom_top_left_selected)
        val topRight = ContextCompat.getDrawable(this, R.drawable.bottom_top_right)
        val topRightSelected = ContextCompat.getDrawable(this, R.drawable.bottom_top_right_selected)

        when (page) {
            // HOME
            R.id.menu_home -> {

                home.background = rectangleSelected
                camera.background = topLeft
                setting.background = rectangle
            }

            // CAMERA
            R.id.menu_camera -> {

                home.background = topRight
                camera.background = rectangleSelected
                setting.background = topLeft
            }

            // SETTINGS
            R.id.menu_setting -> {

                home.background = rectangle
                camera.background = topRight
                setting.background = rectangleSelected
            }
        }
    }
}