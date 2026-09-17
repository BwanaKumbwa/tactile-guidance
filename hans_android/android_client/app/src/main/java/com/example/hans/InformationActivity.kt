package com.example.hans

import com.google.android.material.bottomnavigation.BottomNavigationView
import android.content.Intent
import android.os.Bundle
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity

class InformationActivity : AppCompatActivity() {

    private lateinit var braceletManager: BleManager
    private lateinit var infoBatteryLevel: TextView
    private lateinit var infoBatteryState: TextView
    private lateinit var infoFirmware: TextView


    private val informationListener = object: BleManager.InformationListener{
        override fun onBatteryUpdate(chargeState: Int, batteryLevel: Int) {
            infoBatteryLevel.text = "Battery Level: $batteryLevel%"

            val stateText = when(chargeState) {
                0x00 -> "On Battery"
                0x01 -> "Charging"
                0x02 -> "Fully Charged"
                0x03 -> "Charging"
                else -> "Unknown"
            }
            infoBatteryState.text = "Battery Status: $stateText"
        }

        override fun onFirmwareUpdate( major: Int, minor: Int, protocol: Int) {
            infoFirmware.text = "Firmware version: $major.$minor.$protocol"
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_information)

        braceletManager = BleManagerSingleton.getBraceletManager(this)

        infoBatteryLevel = findViewById(R.id.info_battery_level)
        infoBatteryState = findViewById(R.id.info_battery_state)
        infoFirmware = findViewById(R.id.info_firmware)

        braceletManager.setInformationListener(informationListener)

        if (braceletManager.isConnected()) {
            braceletManager.requestBattery()
            braceletManager.requestFirmware()
        }

        val bottomNav = findViewById<BottomNavigationView>(R.id.bottomNavigation)

        bottomNav.selectedItemId = R.id.menu_setting
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
}