package com.example.hans

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.ViewGroup
import android.widget.RelativeLayout
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.google.android.material.bottomnavigation.BottomNavigationView
import com.google.android.material.switchmaterial.SwitchMaterial

class SettingsActivity : AppCompatActivity() {
    private lateinit var bottomNav: BottomNavigationView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)

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
                    val intent = Intent(this, MainActivity::class.java).apply {
                        flags = Intent.FLAG_ACTIVITY_REORDER_TO_FRONT
                    }
                    startActivity(intent)
                    true
                }
                R.id.menu_setting -> true
                else -> false
            }
        }

        // Intensity Button -> FullIntensityActivity
        findViewById<RelativeLayout?>(R.id.button_intensity)?.setOnClickListener {
            startActivity(Intent(this, FullIntensityActivity::class.java))
        }

        // Pattern Button -> PatternActivity
        findViewById<RelativeLayout?>(R.id.button_pattern)?.setOnClickListener {
            startActivity(Intent(this, PatternActivity::class.java))
        }

        // Information Button -> InformationActivity
        findViewById<RelativeLayout?>(R.id.button_information)?.setOnClickListener {
            startActivity(Intent(this, InformationActivity::class.java))
        }

        // Debug Image Saving Toggle
        // Uses getIdentifier so it compiles whether or not the switch is present in layout yet
        val switchId = resources.getIdentifier("switchSaveDebug", "id", packageName)
        val switchSaveDebug = if (switchId != 0) findViewById<SwitchMaterial>(switchId) else null
        val settingsPrefs = getSharedPreferences("HansSettingsPrefs", Context.MODE_PRIVATE)

        switchSaveDebug?.isChecked = settingsPrefs.getBoolean("SAVE_DEBUG_IMAGES", false)
        switchSaveDebug?.setOnCheckedChangeListener { _, isChecked ->
            settingsPrefs.edit().putBoolean("SAVE_DEBUG_IMAGES", isChecked).apply()
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
            R.id.menu_home -> {
                home.background = rectangleSelected
                camera.background = topLeft
                setting.background = rectangle
            }
            R.id.menu_camera -> {
                home.background = topRight
                camera.background = rectangleSelected
                setting.background = topLeft
            }
            R.id.menu_setting -> {
                home.background = rectangle
                camera.background = topRight
                setting.background = rectangleSelected
            }
        }
    }
}
