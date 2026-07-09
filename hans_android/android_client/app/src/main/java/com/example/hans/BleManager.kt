package com.example.hans

import android.annotation.SuppressLint
import android.bluetooth.*
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import java.util.LinkedList
import java.util.Queue
import java.util.UUID

@SuppressLint("MissingPermission")
class BleManager(private val context: Context) {

    private var bluetoothGatt: BluetoothGatt? = null
    private val adapter: BluetoothAdapter? = (context.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager).adapter
    private val handler = Handler(Looper.getMainLooper())
    private var connectionState = BluetoothProfile.STATE_DISCONNECTED

    // =================================================================
    // UUIDs
    // =================================================================
    private val SERVICE_UUID = UUID.fromString("0000fe51-0000-1000-8000-00805f9b34fb")
    private val KEEP_ALIVE   = UUID.fromString("0000fe02-0000-1000-8000-00805f9b34fb")
    private val WRITE_UUID   = UUID.fromString("0000fe03-0000-1000-8000-00805f9b34fb")
    private val PARAM_UUID   = UUID.fromString("0000fe05-0000-1000-8000-00805f9b34fb")
    private val NOTIFY_UUID  = UUID.fromString("0000fe06-0000-1000-8000-00805f9b34fb")
    private val CCCD_UUID    = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")

    // Robust Command Queue
    private val commandQueue: Queue<Runnable> = LinkedList()
    @Volatile private var isExecuting = false
    private var deviceName = "Unknown"
    private var currentTimeout: Runnable? = null

    fun connect(deviceAddress: String) {
        val device = adapter?.getRemoteDevice(deviceAddress)
        if (device == null) {
            Log.e("BLE", "Device not found: $deviceAddress")
            return
        }

        deviceName = device.name ?: deviceAddress
        Log.i("BLE", "Connecting to $deviceName ($deviceAddress)...")

        synchronized(commandQueue) {
            commandQueue.clear()
            isExecuting = false
        }

        bluetoothGatt = device.connectGatt(context, false, gattCallback)
    }

    // --- ROBUST QUEUE SYSTEM ---
    private fun enqueueCommand(command: Runnable) {
        synchronized(commandQueue) {
            commandQueue.add(command)
            if (!isExecuting) {
                executeNext()
            }
        }
    }

    private fun executeNext() {
        synchronized(commandQueue) {
            if (isExecuting) return
            val command = commandQueue.poll()
            if (command != null) {
                isExecuting = true
                handler.post {
                    // Create a specific timeout for this command execution
                    val timeout = Runnable {
                        Log.w("BLE", "[$deviceName] ⚠️ Command timed out! Advancing queue.")
                        commandCompleted()
                    }
                    currentTimeout = timeout
                    handler.postDelayed(timeout, 1000)

                    // Execute the command
                    command.run()
                }
            }
        }
    }

    private fun commandCompleted() {
        synchronized(commandQueue) {
            currentTimeout?.let { handler.removeCallbacks(it) }
            currentTimeout = null
            isExecuting = false
            executeNext()
        }
    }

    fun writeRawCommand(bytes: ByteArray) {
        if (bytes.isEmpty()) return
        Log.d("BLE", "[$deviceName] ⬇️ Queuing command. Queue size: ${commandQueue.size}")

        enqueueCommand {
            // SILENT KILLER 1: Device disconnected in the background
            if (!isConnected()) {
                Log.e("BLE", "[$deviceName] ❌ DROPPED: Device is not connected!")
                commandCompleted()
                return@enqueueCommand
            }

            // SILENT KILLER 2: Initialization didn't finish properly
            val service = bluetoothGatt?.getService(SERVICE_UUID)
            if (service == null) {
                Log.e("BLE", "[$deviceName] ❌ DROPPED: Service not found! Did init finish?")
                commandCompleted()
                return@enqueueCommand
            }

            val char = service.getCharacteristic(WRITE_UUID)
            if (char == null) {
                Log.e("BLE", "[$deviceName] ❌ DROPPED: WRITE_UUID characteristic not found!")
                commandCompleted()
                return@enqueueCommand
            }

            char.value = bytes
            char.writeType = BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE

            // SILENT KILLER 3: Android BLE stack rejected the command
            val success = bluetoothGatt?.writeCharacteristic(char) ?: false
            if (!success) {
                Log.e("BLE", "[$deviceName] ❌ DROPPED: Android OS rejected writeCharacteristic()")
                commandCompleted()
            } else {
                Log.d("BLE", "[$deviceName] ✓ Write initiated to Bluetooth chip")
            }
        }
    }

    private fun subscribeTo(uuid: UUID, name: String) {
        enqueueCommand {
            val char = bluetoothGatt?.getService(SERVICE_UUID)?.getCharacteristic(uuid)
            if (char != null) {
                bluetoothGatt?.setCharacteristicNotification(char, true)
                val desc = char.getDescriptor(CCCD_UUID)
                if (desc != null) {
                    desc.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
                    if (bluetoothGatt?.writeDescriptor(desc) == false) {
                        commandCompleted()
                    }
                } else {
                    commandCompleted()
                }
            } else {
                commandCompleted()
            }
        }
    }

    private fun writeParam(bytes: ByteArray, name: String) {
        enqueueCommand {
            val char = bluetoothGatt?.getService(SERVICE_UUID)?.getCharacteristic(PARAM_UUID)
            if (char != null) {
                char.value = bytes
                char.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
                if (bluetoothGatt?.writeCharacteristic(char) == false) {
                    commandCompleted()
                }
            } else {
                commandCompleted()
            }
        }
    }

    private val gattCallback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(gatt: BluetoothGatt, status: Int, newState: Int) {
            connectionState = newState
            Log.i("BLE", "[$deviceName] Connection state changed: $newState (status: $status)")

            if (newState == BluetoothProfile.STATE_CONNECTED) {
                Log.i("BLE", "[$deviceName] Connected! Discovering services in 1s...")
                handler.postDelayed({ gatt.discoverServices() }, 1000)
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                synchronized(commandQueue) {
                    commandQueue.clear()
                    isExecuting = false
                }
            }
        }

        override fun onServicesDiscovered(gatt: BluetoothGatt, status: Int) {
            if (status == BluetoothGatt.GATT_SUCCESS) {
                Log.i("BLE", "[$deviceName] Services discovered! Initializing...")

                val service = gatt.getService(SERVICE_UUID)
                if (service == null) return

                subscribeTo(KEEP_ALIVE, "Keep Alive (FE02)")
                subscribeTo(NOTIFY_UUID, "Param Notify (FE06)")

                // SWITCH TO APP MODE
                writeParam(byteArrayOf(0x01, 0x81.toByte(), 0x03), "Set Belt to APP MODE")

                // Clear existing vibrations
                val stopBytes = byteArrayOf(0x30, 0xFF.toByte())
                writeRawCommand(stopBytes)

                handler.postDelayed({
                    Log.i("BLE", "[$deviceName] ✓✓✓ READY FOR VIBRATION COMMANDS ✓✓✓")
                }, 1000)
            }
        }

        override fun onDescriptorWrite(gatt: BluetoothGatt, descriptor: BluetoothGattDescriptor, status: Int) {
            commandCompleted()
        }

        override fun onCharacteristicWrite(gatt: BluetoothGatt, characteristic: BluetoothGattCharacteristic, status: Int) {
            if (status != BluetoothGatt.GATT_SUCCESS) {
                Log.e("BLE", "[$deviceName] ❌ Write failed! Status: $status")
            }
            commandCompleted()
        }

        @Deprecated("Deprecated in Java")
        override fun onCharacteristicChanged(gatt: BluetoothGatt, characteristic: BluetoothGattCharacteristic) {
            if (characteristic.uuid == KEEP_ALIVE) {
                enqueueCommand {
                    val char = gatt.getService(SERVICE_UUID)?.getCharacteristic(KEEP_ALIVE)
                    if (char != null) {
                        char.value = byteArrayOf(0x01)
                        char.writeType = BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE
                        if (bluetoothGatt?.writeCharacteristic(char) == false) {
                            commandCompleted()
                        }
                    } else {
                        commandCompleted()
                    }
                }
            }
        }
    }

    fun testVibration() {
        val testCommand = byteArrayOf(
            0x01, 0x00, 60.toByte(), 0x00, 0x00, 0x00, 0x00,
            0x00.toByte(), 0x00.toByte(), 0x00, 0x00, 0x00,
            0x64.toByte(), 0x00, 0x00, 0x00, 0x00, 0x00
        )
        writeRawCommand(testCommand)
        Log.i("BLE", "[$deviceName] Test vibration sent!")
    }

    fun disconnect() {
        synchronized(commandQueue) {
            commandQueue.clear()
        }
        bluetoothGatt?.disconnect()
        bluetoothGatt?.close()
        bluetoothGatt = null
        connectionState = BluetoothProfile.STATE_DISCONNECTED
    }

    fun isConnected(): Boolean {
        return connectionState == BluetoothProfile.STATE_CONNECTED
    }
}