package com.example.hans

import com.google.android.material.bottomnavigation.BottomNavigationView
import android.content.Intent
import android.os.Bundle
import android.widget.Button
import androidx.appcompat.app.AppCompatActivity
import android.widget.ImageView

class BluetoothConnectedActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_bluetooth_connected)

        // Find the button
        val connectedButton = findViewById<Button>(R.id.button_connected)
        val deviceType = intent.getStringExtra("device_type") ?: "bracelet"

        // When clicked → go to BluetoothActivity
        connectedButton.text = when (deviceType) {
            "belt" -> "Belt connected"
            "bracelet" -> "Bracelet connected"
            else -> "Device connected"
        }

        connectedButton.setOnClickListener {
            startActivity(
                Intent(this, BluetoothActivity::class.java).apply {
                    putExtra("device_type", deviceType)
                }
            )
        }

        val bottomNav = findViewById<BottomNavigationView>(R.id.bottomNavigation)

        bottomNav.selectedItemId = R.id.menu_home
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