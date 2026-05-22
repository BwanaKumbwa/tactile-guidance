package com.example.hans

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
    private lateinit var seekRight: SeekBar
    private lateinit var seekBottom: SeekBar
    private lateinit var seekLeft: SeekBar

    private lateinit var topValue: EditText
    private lateinit var rightValue: EditText
    private lateinit var bottomValue: EditText
    private lateinit var leftValue: EditText

    private lateinit var homeBtn: ImageView
    private lateinit var settingBtn: ImageView
    private lateinit var cameraBtn: ImageView

    private val PREFS_NAME = "FullIntensityPrefs"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_fullintensity)

        // Binding SeekBars
        seekTop = findViewById(R.id.seekTop)
        seekRight = findViewById(R.id.seekRight)
        seekBottom = findViewById(R.id.seekBottom)
        seekLeft = findViewById(R.id.seekLeft)

        // Binding EditTexts
        topValue = findViewById(R.id.topValue)
        rightValue = findViewById(R.id.rightValue)
        bottomValue = findViewById(R.id.bottomValue)
        leftValue = findViewById(R.id.leftValue)

        // Binding Buttons
        homeBtn = findViewById(R.id.Home)
        settingBtn = findViewById(R.id.Setting)
        cameraBtn = findViewById(R.id.Camera_command)

        // Load saved values
        loadSeekBarValues()

        // Setup SeekBar + EditText bi-directional
        setupSeekBarWithEdit(seekTop, topValue, "topIntensity")
        setupSeekBarWithEdit(seekRight, rightValue, "rightIntensity")
        setupSeekBarWithEdit(seekBottom, bottomValue, "bottomIntensity")
        setupSeekBarWithEdit(seekLeft, leftValue, "leftIntensity")

        // Button listeners
        homeBtn.setOnClickListener { startActivity(Intent(this, BluetoothActivity::class.java)) }
        settingBtn.setOnClickListener { startActivity(Intent(this, SettingsActivity::class.java)) }
        cameraBtn.setOnClickListener { startActivity(Intent(this, MainActivity::class.java)) }

        val selectButton = findViewById<Button>(R.id.button_select_intensity)
        selectButton.setOnClickListener {
            saveSeekBarValue("topIntensity", seekTop.progress)
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

        seekTop.progress = prefs.getInt("topIntensity", 0)
        topValue.setText(seekTop.progress.toString())

        seekRight.progress = prefs.getInt("rightIntensity", 0)
        rightValue.setText(seekRight.progress.toString())

        seekBottom.progress = prefs.getInt("bottomIntensity", 0)
        bottomValue.setText(seekBottom.progress.toString())

        seekLeft.progress = prefs.getInt("leftIntensity", 0)
        leftValue.setText(seekLeft.progress.toString())
    }
}