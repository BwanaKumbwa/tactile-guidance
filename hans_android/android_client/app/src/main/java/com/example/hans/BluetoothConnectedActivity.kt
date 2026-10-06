package com.example.hans

import com.google.android.material.bottomnavigation.BottomNavigationView
import android.content.Intent
import android.os.Bundle
import android.view.ViewGroup
import android.widget.Button
import androidx.appcompat.app.AppCompatActivity
import android.widget.ImageView
import androidx.core.content.ContextCompat

class BluetoothConnectedActivity : AppCompatActivity() {
    private lateinit var bottomNav: BottomNavigationView
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

        bottomNav = findViewById(R.id.bottomNavigation)
        bottomNav.selectedItemId = R.id.menu_home
        bottomNav.post {
            updateBottomNavBackground(R.id.menu_home)
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