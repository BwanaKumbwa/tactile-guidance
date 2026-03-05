package com.example.hans

import android.content.Intent
import android.os.Bundle
import android.widget.Button
import androidx.appcompat.app.AppCompatActivity
import android.widget.ImageView


class BluetoothNotConnectedActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_bluetooth_notconnected)

        // Find the button
        val notconnectedButton = findViewById<Button>(R.id.button_not_connected)

        // When clicked → go to BluetoothActivity
        notconnectedButton.setOnClickListener {
            startActivity(
                Intent(this, BluetoothActivity::class.java)
            )
        }

        // Home icon: go to BluetoothActivity
        val homeIcon = findViewById<ImageView>(R.id.Home)
        homeIcon.setOnClickListener {
            startActivity(
                Intent(this, BluetoothActivity::class.java)
            )
            finish() // optional: prevents stacking activities
        }

        // Setting icon: go to SettingsActivity
        val settingIcon = findViewById<ImageView>(R.id.Setting)
        settingIcon.setOnClickListener {
            startActivity(
                Intent(this, SettingsActivity::class.java)
            )
            finish() // optional: prevents stacking activities
        }

        // Camera icon: go to MainActivity
        val cameraIcon = findViewById<ImageView>(R.id.Camera_command)
        cameraIcon.setOnClickListener {
            startActivity(
                Intent(this, MainActivity::class.java)
            )
            finish() // optional: prevents stacking activities
        }
    }
}
