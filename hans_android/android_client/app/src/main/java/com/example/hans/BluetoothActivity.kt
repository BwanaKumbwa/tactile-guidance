package com.example.hans

import android.content.Intent
import android.os.Bundle
import android.util.Log
import android.widget.ImageView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity

class BluetoothActivity : AppCompatActivity() {

    // BLE MAC Addresses
    private val MAC_BRACELET = "00:A0:50:93:8A:AA"
    private val MAC_BELT     = "00:A0:50:DA:2B:54"

    // Use singleton instead of local instances
    private lateinit var braceletManager: BleManager
    private lateinit var beltManager: BleManager

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_bluetooth)

        // Get singleton instances
        braceletManager = BleManagerSingleton.getBraceletManager(this)
        beltManager = BleManagerSingleton.getBeltManager(this)

        // Start connection check
        checkBleConnection()

        // Home icon: go to BluetoothActivity
        val homeIcon = findViewById<ImageView>(R.id.Home)
        homeIcon.setOnClickListener {
            startActivity(Intent(this, BluetoothActivity::class.java))
            finish()
        }

        // Setting icon: go to SettingsActivity
        val settingIcon = findViewById<ImageView>(R.id.Setting)
        settingIcon.setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
            finish()
        }

        // Camera icon: go to MainActivity
        val cameraIcon = findViewById<ImageView>(R.id.Camera_command)
        cameraIcon.setOnClickListener {
            startActivity(Intent(this, MainActivity::class.java))
            finish()
        }
    }

    private fun checkBleConnection() {
        Thread {
            try {
                Log.d("HANS", "🔌 Connecting bracelet...")
                braceletManager.connect(MAC_BRACELET)
                Thread.sleep(3000)

                Log.d("HANS", "🔌 Connecting belt...")
                beltManager.connect(MAC_BELT)
                Thread.sleep(3000)

                val braceletConnected = braceletManager.isConnected()
                val beltConnected = beltManager.isConnected()

                Log.d("HANS", "✓ Bracelet: $braceletConnected, Belt: $beltConnected")

                runOnUiThread {
                    when {
                        braceletConnected || beltConnected -> {
                            Log.d("HANS", "✓ At least one device connected → MainActivity")
                            startActivity(Intent(this@BluetoothActivity, BluetoothConnectedActivity::class.java))
                            finish()
                        }
                        else -> {
                            Log.w("HANS", "❌ No devices connected → retry screen")
                            startActivity(Intent(this@BluetoothActivity, BluetoothNotConnectedActivity::class.java))
                            finish()
                        }
                    }
                }

            } catch (e: Exception) {
                Log.e("HANS", "BLE Connection Error", e)
                runOnUiThread {
                    Toast.makeText(this@BluetoothActivity, "BLE Connection Failed", Toast.LENGTH_SHORT).show()
                    startActivity(Intent(this@BluetoothActivity, BluetoothNotConnectedActivity::class.java))
                    finish()
                }
            }
        }.start()
    }

    override fun onDestroy() {
        super.onDestroy()
        // Don't disconnect here! Keep connection alive for MainActivity
        // Only disconnect when app truly closes
    }
}