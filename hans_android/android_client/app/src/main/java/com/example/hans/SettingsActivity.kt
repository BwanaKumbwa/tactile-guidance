package com.example.hans

import com.google.android.material.bottomnavigation.BottomNavigationView
import android.content.Intent
import android.os.Bundle
import android.widget.Button
import androidx.appcompat.app.AppCompatActivity
import android.widget.ImageView
import android.widget.RelativeLayout

class SettingsActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)

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

                R.id.menu_setting -> true

                else -> false
            }
        }

        // Find the button
        val intensityButton = findViewById<RelativeLayout>(R.id.button_intensity)

        // When clicked → go to IntensityActivity
        intensityButton.setOnClickListener {
            startActivity(
                Intent(this, FullIntensityActivity::class.java)
            )
        }

        // Find the button
        val patternButton = findViewById<RelativeLayout>(R.id.button_pattern)

        // When clicked → go to PatternActivity
        patternButton.setOnClickListener {
            startActivity(
                Intent(this, PatternActivity::class.java)
            )
        }
    }
}