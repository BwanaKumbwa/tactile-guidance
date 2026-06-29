package com.example.hans

import com.google.android.material.bottomnavigation.BottomNavigationView
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.text.Editable
import android.text.TextWatcher
import android.widget.EditText
import android.widget.ImageView
import android.widget.SeekBar
import androidx.appcompat.app.AppCompatActivity

class FullIntensityActivity : AppCompatActivity() {

    private lateinit var seekTop: SeekBar
    private lateinit var seekTopFront: SeekBar
    private lateinit var seekTopBack: SeekBar
    private lateinit var seekRight: SeekBar
    private lateinit var seekBottom: SeekBar
    private lateinit var seekLeft: SeekBar

    private lateinit var topValue: EditText
    private lateinit var topFrontValue: EditText
    private lateinit var topBackValue: EditText
    private lateinit var rightValue: EditText
    private lateinit var bottomValue: EditText
    private lateinit var leftValue: EditText

    private val PREFS_NAME = "FullIntensityPrefs"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_fullintensity)

        // Binding SeekBars
        seekTop = findViewById(R.id.seekTop)
        seekTopFront = findViewById(R.id.seekTopFront)
        seekTopBack = findViewById(R.id.seekTopBack)
        seekRight = findViewById(R.id.seekRight)
        seekBottom = findViewById(R.id.seekBottom)
        seekLeft = findViewById(R.id.seekLeft)

        // Binding EditTexts
        topValue = findViewById(R.id.topValue)
        topFrontValue = findViewById(R.id.topFrontValue)
        topBackValue = findViewById(R.id.topBackValue)
        rightValue = findViewById(R.id.rightValue)
        bottomValue = findViewById(R.id.bottomValue)
        leftValue = findViewById(R.id.leftValue)

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

        // Load saved values
        loadSeekBarValues()

        // Setup SeekBar + EditText bi-directional
        setupSeekBarWithEdit(seekTop, topValue, "topIntensity")
        setupSeekBarWithEdit(seekTopFront, topFrontValue, "topFrontIntensity")
        setupSeekBarWithEdit(seekTopBack, topBackValue, "topBackIntensity")
        setupSeekBarWithEdit(seekRight, rightValue, "rightIntensity")
        setupSeekBarWithEdit(seekBottom, bottomValue, "bottomIntensity")
        setupSeekBarWithEdit(seekLeft, leftValue, "leftIntensity")

        val selectButton = findViewById<Button>(R.id.button_select_intensity)
        selectButton.setOnClickListener {
            saveSeekBarValue("topIntensity", seekTop.progress)
            saveSeekBarValue("topFrontIntensity", seekTopFront.progress)
            saveSeekBarValue("topBackIntensity", seekTopBack.progress)
            saveSeekBarValue("rightIntensity", seekRight.progress)
            saveSeekBarValue("bottomIntensity", seekBottom.progress)
            saveSeekBarValue("leftIntensity", seekLeft.progress)

            finish()
        }

    }

    private fun setupSeekBarWithEdit(seekBar: SeekBar, editText: EditText, key: String) {
        // Update EditText saat SeekBar digeser
        seekBar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(seekBar: SeekBar?, progress: Int, fromUser: Boolean) {
                if (editText.text.toString() != progress.toString()) {
                    editText.setText(progress.toString())
                }
                saveSeekBarValue(key, progress)
            }
            override fun onStartTrackingTouch(seekBar: SeekBar?) {}
            override fun onStopTrackingTouch(seekBar: SeekBar?) {}
        })

        // Update SeekBar saat EditText diketik
        editText.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            override fun afterTextChanged(s: Editable?) {
                val value = s.toString().toIntOrNull() ?: 0
                if (value != seekBar.progress) {
                    seekBar.progress = value.coerceIn(0, 100)
                }
            }
        })
    }

    private fun saveSeekBarValue(key: String, value: Int) {
        val prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        with(prefs.edit()) {
            putInt(key, value)
            apply()
        }
    }

    private fun loadSeekBarValues() {
        val prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)


        seekLeft.progress = prefs.getInt("leftIntensity", 0)
        leftValue.setText(seekLeft.progress.toString())

        seekBottom.progress = prefs.getInt("bottomIntensity", 0)
        bottomValue.setText(seekBottom.progress.toString())

        seekRight.progress = prefs.getInt("rightIntensity", 0)
        rightValue.setText(seekRight.progress.toString())

        seekTop.progress = prefs.getInt("topIntensity", 0)
        topValue.setText(seekTop.progress.toString())

        seekTopFront.progress = prefs.getInt("topFrontIntensity", 0)
        topFrontValue.setText(seekTopFront.progress.toString())

        seekTopBack.progress = prefs.getInt("topBackIntensity", 0)
        topBackValue.setText(seekTopBack.progress.toString())
    }
}