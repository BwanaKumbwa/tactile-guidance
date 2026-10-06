package com.example.hans

import com.google.android.material.bottomnavigation.BottomNavigationView
import android.content.Intent
import android.content.SharedPreferences
import android.os.Bundle
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import java.util.Locale

class InformationActivity : AppCompatActivity() {

    // BLE
    private lateinit var braceletManager: BleManager
    private lateinit var beltManager: BleManager
    private lateinit var prefs: SharedPreferences
    private val MAC_BRACELET = BuildConfig.MAC_BRACELET
    private val MAC_BELT = BuildConfig.MAC_BELT
    private lateinit var bottomNav: BottomNavigationView

    // UI
    private lateinit var iconBracelet: ImageView
    private lateinit var iconBelt: ImageView
    private lateinit var infoBraceletBatteryLevel: TextView
    private lateinit var infoBraceletBatteryState: TextView
    private lateinit var infoBraceletFirmware: TextView
    private lateinit var infoBeltBatteryLevel: TextView
    private lateinit var infoBeltBatteryState: TextView
    private lateinit var infoBeltFirmware: TextView

    // BRACELET LISTENER
    private val braceletInformationListener =
        object : BleManager.InformationListener {
            override fun onBatteryUpdate(chargeState: Int, batteryLevel: Float) {
                runOnUiThread {
                    infoBraceletBatteryLevel.text =
                        String.format(
                            Locale.US,
                            "%.2f%%",
                            batteryLevel
                        )

                    infoBraceletBatteryState.text =
                        when (chargeState) {
                            0x00 -> "On Battery"
                            0x01 -> "Charging"
                            0x02 -> "Fully Charged"
                            0x03 -> "Not Charging"
                            else -> "Unknown"
                        }
                }
            }

            override fun onFirmwareUpdate(
                version: String
            ) {
                runOnUiThread {
                    infoBraceletFirmware.text = version
                }
            }
        }

    // BELT LISTENER
    private val beltInformationListener =
        object : BleManager.InformationListener {

            override fun onBatteryUpdate(chargeState: Int, batteryLevel: Float) {
                runOnUiThread {

                    infoBeltBatteryLevel.text =
                        String.format(
                            Locale.US,
                            "%.2f%%",
                            batteryLevel
                        )

                    infoBeltBatteryState.text =
                        when (chargeState) {
                            0x00 -> "Disconnected"
                            0x01 -> "Connecting"
                            0x02 -> "Connected"
                            0x03 -> "Disconnecting"
                            else -> "Unknown"
                        }
                }
            }

            override fun onFirmwareUpdate(version: String) {
                runOnUiThread {
                    infoBeltFirmware.text = version
                }
            }
        }

    // CREATE
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_information)

        // SAME SINGLETON
        braceletManager = BleManagerSingleton.getBraceletManager(applicationContext)
        beltManager = BleManagerSingleton.getBeltManager(applicationContext)

        // PREFS
        prefs = getSharedPreferences("ble_state", MODE_PRIVATE)

        // UI
        iconBracelet = findViewById(R.id.iconBracelet)
        iconBelt = findViewById(R.id.iconBelt)
        infoBraceletBatteryLevel = findViewById(R.id.info_bracelet_battery_level)
        infoBraceletBatteryState = findViewById(R.id.info_bracelet_battery_state)
        infoBraceletFirmware = findViewById(R.id.info_bracelet_firmware)

        infoBeltBatteryLevel = findViewById(R.id.info_belt_battery_level)
        infoBeltBatteryState = findViewById(R.id.info_belt_battery_state)
        infoBeltFirmware = findViewById(R.id.info_belt_firmware)

        // LISTENER
        braceletManager.setInformationListener(braceletInformationListener)
        beltManager.setInformationListener(beltInformationListener)

        // INITIAL STATUS
        updateConnectionStatus()

        // NAVIGATION
        bottomNav = findViewById(R.id.bottomNavigation)
        bottomNav.selectedItemId = R.id.menu_setting
        bottomNav.post {
            updateBottomNavBackground(R.id.menu_setting)
        }
        bottomNav.setOnItemSelectedListener { item ->

            when (item.itemId) {
                R.id.menu_home -> {
                    startActivity(Intent(this, BluetoothActivity::class.java))
                    finish()
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

    // RESUME
    override fun onResume() {
        super.onResume()
        updateConnectionStatus()
        loadInformation()
    }

    // CONNECTION STATUS
    private fun updateConnectionStatus() {
        val braceletConnected = braceletManager.isConnected()
        val beltConnected = beltManager.isConnected()
        val braceletSaved = prefs.getBoolean("bracelet", false)
        val beltSaved = prefs.getBoolean("belt", false)

        if (braceletConnected || braceletSaved) {
            iconBracelet.setBackgroundResource(R.drawable.bg_green_solid)

        } else {
            iconBracelet.setBackgroundResource(R.drawable.bg_red_solid)
        }

        // Belt
        if (beltConnected || beltSaved) {
            iconBelt.setBackgroundResource(R.drawable.bg_green_solid)

        } else {
            iconBelt.setBackgroundResource(R.drawable.bg_red_solid)
        }
    }

    // LOAD INFORMATION
    private fun loadInformation() {

        // BRACELET
        if (braceletManager.isConnected()) {
            iconBracelet.setBackgroundResource(R.drawable.bg_green_solid)
            braceletManager.requestBattery()
            braceletManager.requestFirmware()

        } else {
            val saved = prefs.getBoolean("bracelet", false)
            if (saved) {
                iconBracelet.setBackgroundResource(R.drawable.bg_green_solid)
                reconnectBracelet()

            } else {
                iconBracelet.setBackgroundResource(R.drawable.bg_red_solid)
            }
        }

        // BELT
        if (beltManager.isConnected()) {
            iconBelt.setBackgroundResource(R.drawable.bg_green_solid)
            beltManager.requestBattery()
            beltManager.requestFirmware()

        } else {
            val saved = prefs.getBoolean("belt", false)
            if (saved) {
                iconBelt.setBackgroundResource(R.drawable.bg_green_solid)
                reconnectBelt()

            } else {
                iconBelt.setBackgroundResource(R.drawable.bg_red_solid)
            }
        }
    }

    // RECONNECT BRACELET
    private fun reconnectBracelet() {
        Thread {
            try {
                braceletManager.connect(MAC_BRACELET)
                val connected = braceletManager.isConnected()

                runOnUiThread {
                    if (connected) {
                        prefs.edit()
                            .putBoolean("bracelet", true)
                            .apply()

                        iconBracelet.setBackgroundResource(R.drawable.bg_green_solid)
                        braceletManager.requestBattery()
                        braceletManager.requestFirmware()

                    } else {
                        iconBracelet.setBackgroundResource(R.drawable.bg_red_solid)
                    }
                }

            } catch (e: Exception) {

                runOnUiThread {
                    iconBracelet.setBackgroundResource(R.drawable.bg_red_solid)
                }
            }
        }.start()
    }

    // RECONNECT BELT
    private fun reconnectBelt() {
        Thread {
            try {
                beltManager.connect(MAC_BELT)
                Thread.sleep(1500)
                val connected = beltManager.isConnected()

                runOnUiThread {
                    if (connected) {
                        prefs.edit()
                            .putBoolean("belt", true)
                            .apply()

                        iconBelt.setBackgroundResource(R.drawable.bg_green_solid)
                        beltManager.requestBattery()
                        beltManager.requestFirmware()

                    } else {
                        iconBelt.setBackgroundResource(R.drawable.bg_red_solid)
                    }
                }

            } catch (e: Exception) {

                runOnUiThread {
                    iconBelt.setBackgroundResource(R.drawable.bg_red_solid)
                }
            }
        }.start()
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