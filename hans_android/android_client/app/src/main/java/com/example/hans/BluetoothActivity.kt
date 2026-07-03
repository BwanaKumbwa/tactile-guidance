package com.example.hans

import android.content.Intent
import android.os.Bundle
import android.widget.ImageView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.bottomnavigation.BottomNavigationView
import android.content.SharedPreferences
import androidx.constraintlayout.widget.ConstraintLayout

class BluetoothActivity : AppCompatActivity() {

    // BLE MAC Addresses
    private val MAC_BRACELET = "00:A0:50:65:73:20" // UPDATE
    private val MAC_BELT     = "00:A0:50:39:96:11" // UPDATE

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

        // 1. Initialize BLE Managers
        braceletManager = BleManager(this)
        beltManager = BleManager(this)

        bottomNav = findViewById(R.id.bottomNavigation)

        prefs = getSharedPreferences("ble_state", MODE_PRIVATE)
        connectBelt = findViewById(R.id.button_belt)
        connectBracelet = findViewById(R.id.button_bracelet)

        beltIcon = findViewById(R.id.icon_belt)
        braceletIcon = findViewById(R.id.icon_bracelet)

        beltBluetooth = findViewById(R.id.iconLeftBelt)
        braceletBluetooth = findViewById(R.id.iconLeftBracelet)

        // default UI
        renderSavedState()

        // 2. Start connection check
        connectBelt.setOnClickListener {
            if (isConnecting) return@setOnClickListener
            isConnecting = true
            connectBelt.isEnabled = false

            Toast.makeText(
                this,
                "Connecting belt",
                Toast.LENGTH_SHORT
            ).show()

            checkBleConnection(connectBracelet = false, connectBelt = true)
        }

        connectBracelet.setOnClickListener {
            if (isConnecting) return@setOnClickListener
            isConnecting = true
            connectBracelet.isEnabled = false

            Toast.makeText(
                this,
                "Connecting bracelet",
                Toast.LENGTH_SHORT
            ).show()

            checkBleConnection(connectBracelet = true, connectBelt = false)
        }

        bottomNav.selectedItemId = R.id.menu_home
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
                R.id.menu_home -> true

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

    // 4. BLE Connection Logic
    private fun checkBleConnection(connectBracelet: Boolean, connectBelt: Boolean) {

        val deviceType = when {
            connectBelt -> "belt"
            connectBracelet -> "bracelet"
            else -> "unknown"
        }

        Thread {
            try {

                var beltConnected = prefs.getBoolean("belt", false)
                var braceletConnected = prefs.getBoolean("bracelet", false)

                // CONNECT BELT
                if (connectBelt) {
                    beltManager.connect(MAC_BELT)
                    Thread.sleep(1500)

                    beltConnected = beltManager.isConnected()
                }

                // CONNECT BRACELET
                if (connectBracelet) {
                    braceletManager.connect(MAC_BRACELET)
                    Thread.sleep(1500)

                    braceletConnected = braceletManager.isConnected()
                }

                runOnUiThread {
                    isConnecting = false

                    // UPDATE UI
                    updateIconFromState(beltConnected, braceletConnected)

                    // SAVE STATE
                    prefs.edit()
                        .putBoolean("belt", beltConnected)
                        .putBoolean("bracelet", braceletConnected)
                        .apply()

                    val success = when {
                        connectBelt -> beltConnected
                        connectBracelet -> braceletConnected
                        else -> false
                    }

                    val target = if (success)
                        BluetoothConnectedActivity::class.java
                    else
                        BluetoothNotConnectedActivity::class.java

                    startActivity(
                        Intent(this, target).apply {
                            putExtra("device_type", deviceType)
                        }
                    )
                    finish()
                }

            } catch (e: Exception) {
                runOnUiThread {
                    isConnecting = false

                    startActivity(
                        Intent(this, BluetoothNotConnectedActivity::class.java).apply {
                            putExtra("device_type", deviceType)
                        }
                    )
                    finish()
                }
            }
        }.start()
    }

    // ICON UPDATE
    private fun updateIconFromState(
        beltConnected: Boolean,
        braceletConnected: Boolean) {

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

    // LOAD SAVED STATE
    private fun renderSavedState() {

        val belt = prefs.getBoolean("belt", false)
        val bracelet = prefs.getBoolean("bracelet", false)

        updateIconFromState(belt, bracelet)
    }

    override fun onResume() {
        super.onResume()
        renderSavedState()
    }

    override fun onDestroy() {
        super.onDestroy()
        braceletManager.disconnect()
        beltManager.disconnect()
    }
}