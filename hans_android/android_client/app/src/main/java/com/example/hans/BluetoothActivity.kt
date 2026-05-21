package com.example.hans

import android.content.Intent
import android.os.Bundle
import android.widget.ImageView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONObject

class BluetoothActivity : AppCompatActivity() {

    // BLE MAC Addresses
    private val MAC_BRACELET = "00:A0:50:93:8A:AA" // UPDATE
    private val MAC_BELT     = "00:A0:50:DA:2B:54" // UPDATE

    private lateinit var braceletManager: BleManager
    private lateinit var beltManager: BleManager

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_bluetooth)

        // 1. Initialize BLE Managers
        braceletManager = BleManager(this)
        beltManager = BleManager(this)

        // 2. Start connection check
        checkBleConnection()

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

    // 4. BLE Connection Logic
    private fun checkBleConnection() {
        // Connect in background thread to avoid blocking UI
        Thread {
            try {
                // --- Connect Bracelet ---
                braceletManager.connect(MAC_BRACELET)
                Thread.sleep(1500) // wait a bit for connection to settle

                // --- Connect Belt ---
                beltManager.connect(MAC_BELT)
                Thread.sleep(1500)

                // Check if either device connected
                val braceletConnected = braceletManager.isConnected()
                val beltConnected = beltManager.isConnected()

                runOnUiThread {
                    when {
                        braceletConnected || beltConnected -> {
                            // At least one device connected → go to BluetoothConnectedActivity
                            startActivity(Intent(this, BluetoothConnectedActivity::class.java))
                            finish()
                        }
                        else -> {
                            // No device connected → go to BluetoothNotConnectedActivity
                            startActivity(Intent(this, BluetoothNotConnectedActivity::class.java))
                            finish()
                        }
                    }
                }

            } catch (e: Exception) {
                e.printStackTrace()
                runOnUiThread {
                    Toast.makeText(this, "BLE Connection Failed", Toast.LENGTH_SHORT).show()
                    startActivity(Intent(this, BluetoothNotConnectedActivity::class.java))
                    finish()
                }
            }
        }.start()
    }

    override fun onDestroy() {
        super.onDestroy()
        braceletManager.disconnect()
        beltManager.disconnect()
    }
}