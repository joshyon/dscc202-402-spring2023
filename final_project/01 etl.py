# Databricks notebook source
# MAGIC %run ./includes/includes

# COMMAND ----------

dbutils.widgets.removeAll()

dbutils.widgets.text('01.start_date', "2021-10-01")
dbutils.widgets.text('02.end_date', "2023-03-01")
dbutils.widgets.text('03.hours_to_forecast', '4')
dbutils.widgets.text('04.promote_model', 'No')

start_date = str(dbutils.widgets.get('01.start_date'))
end_date = str(dbutils.widgets.get('02.end_date'))
hours_to_forecast = int(dbutils.widgets.get('03.hours_to_forecast'))
promote_model = bool(True if str(dbutils.widgets.get('04.promote_model')).lower() == 'yes' else False)

print(start_date,end_date,hours_to_forecast, promote_model)
#print("YOUR CODE HERE...")

# COMMAND ----------

# MAGIC %md
# MAGIC The cell below defines the readStream for the historic bike trip data

# COMMAND ----------

#This cell defines the readStreaming for the historic_bike_data
historic_bike_df = (spark.readStream
 .csv(BIKE_TRIP_DATA_PATH, header="true", schema= 
     ("""ride_id string,
  rideable_type string,
  started_at timestamp,
  ended_at timestamp,
  start_station_name string,
  start_station_id string,
  end_station_name string,
  end_station_id string,
  start_lat double,
  start_lng double,
  end_lat double,
  end_lng double,
  member_casual string""")))


# COMMAND ----------

# MAGIC %md
# MAGIC This cell defines the writeStream for the historic bike trip data, creating a bronze delta table in the GROUP_DATA_PATH for the historic bike trip data.

# COMMAND ----------

#This cell completes the writeStream for the historic_bike_data
(historic_bike_df.writeStream
 .partitionBy("start_station_name")
 .option("checkpointLocation", f"{GROUP_DATA_PATH}/bronze/historic_bike/checkpoints")
 .option("mergeSchema", "true")
 .outputMode("append")
 .trigger(availableNow=True)
 .format("delta")
 .start(f"{GROUP_DATA_PATH}/bronze_historic_bike_trip.delta")
)



# COMMAND ----------

#saves bronze historic bike trip data as a managed table in our group database.

historic_bike_data_table = spark.read.format("delta").load(f"{GROUP_DATA_PATH}/bronze_historic_bike_trip.delta")
historic_bike_data_table.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable("G09_db.bronze_historic_bike_trip")

# COMMAND ----------

# MAGIC %sql
# MAGIC OPTIMIZE bronze_historic_bike_trip
# MAGIC ZORDER BY (started_at)

# COMMAND ----------

# MAGIC %md
# MAGIC The cell below defines the readStream for the historic weather data

# COMMAND ----------

#This is the readStream for the historic_weather_data
historic_weather_df = (spark.readStream
 .csv(NYC_WEATHER_FILE_PATH, header="true", schema= 
     ("""dt integer,
  temp double,
  feels_like double,
  pressure integer,
  humidity integer,
  dew_point double,
  uvi double,
  clouds integer,
  visibility integer,
  wind_speed double,
  wind_deg integer,
  pop double,
  snow_1h double,
  id integer,
  main string,
  description string,
  icon string,
  loc string,
  lat double,
  lon double,
  timezone string,
  timezone_offset integer,
  rain_1h double""")))

# COMMAND ----------

# MAGIC %md
# MAGIC This cell defines the writeStream for the historic weather data, creating a bronze delta table in the GROUP_DATA_PATH for the historic weather data.

# COMMAND ----------

#This is the writeStream for the historic_weather_data
(historic_weather_df.writeStream
 .partitionBy("main")
 .option("checkpointLocation", f"{GROUP_DATA_PATH}/bronze/historic_weather/checkpoints")
 .option("mergeSchema", "true")
 .outputMode("append")
 .trigger(availableNow=True)
 .format("delta")
 .start(f"{GROUP_DATA_PATH}/bronze_historic_weather.delta")
)



# COMMAND ----------

#saves bronze historic weather data as a managed table in our group database.

historic_weather_table = spark.read.format("delta").load(f"{GROUP_DATA_PATH}/bronze_historic_weather.delta")
historic_weather_table.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable("G09_db.bronze_historic_weather_data")

# COMMAND ----------

# MAGIC %sql
# MAGIC OPTIMIZE bronze_historic_weather_data
# MAGIC ZORDER BY (dt)

# COMMAND ----------

# MAGIC %fs ls /FileStore/tables/G09

# COMMAND ----------

from pyspark.sql.functions import col, window, count, when, sum


bike_trip_info = (
    spark
    .readStream.format("delta")
    .option("maxFilesPerTrigger", 1)
    .load("dbfs:/FileStore/tables/G09/bronze_historic_bike_trip.delta")
)


streamingDF = (
    bike_trip_info
    .filter((col("start_station_name") == "E 33 St & 1 Ave") | (col("end_station_name") == "E 33 St & 1 Ave"))
    .withWatermark("started_at", "2 hours")
    .groupBy(window(col("started_at"), "1 hour"), col("start_station_name") == "E 33 St & 1 Ave").agg(count("*").alias("count"))
    .withColumnRenamed("(start_station_name = E 33 St & 1 Ave)", "indicator")
)

checkpoint_path = f"{GROUP_DATA_PATH}/silver/silver_indicator_df/checkpoint"
output_path = f"{GROUP_DATA_PATH}/silver_indicator_df.delta"

devices_query = (
                streamingDF
                 .writeStream
                 .outputMode("append")
                 .format("delta")
                 .queryName("write_silver_indicator_df")
                 .trigger(availableNow=True)
                 .option("mergeSchema", "true")
                 .option("checkpointLocation", checkpoint_path)
                 .start(output_path)
                )

# COMMAND ----------

from pyspark.sql.functions import dayofweek

silver_indicator_df = (
    spark
    .readStream.format("delta")
    .option("maxFilesPerTrigger", 1)
    .load("dbfs:/FileStore/tables/G09/silver_indicator_df.delta")
)

success_count = sum(when(col("indicator") == True, col("count")))

error_count = sum(when(col("indicator") == False, col("count")))

streaming_silver_indicatorDF = (
    silver_indicator_df
    .groupBy("window").agg(success_count.alias("true"), error_count.alias("false"))
    .na.fill(0)
    .withColumnRenamed("window", "window")
    .withColumnRenamed("false", "bikes_returning")
    .withColumnRenamed("true", "bikes_leaving")
    .withColumn("bikes_net_change", col("bikes_returning")-col("bikes_leaving"))
    .withColumn("date_timestamp", col("window")["start"])
    .withColumn("weekday_indicator", (dayofweek(col("date_timestamp")) >= 2) & (dayofweek(col("date_timestamp")) <= 6))
    .drop("window")
)

checkpoint_path = f"{GROUP_DATA_PATH}/silver/silver_hourly_raw/checkpoint"
output_path = f"{GROUP_DATA_PATH}/silver_hourly_raw.delta"

silver_query = (
                streaming_silver_indicatorDF
                 .writeStream
                 .outputMode("complete")
                 .format("delta")
                 .queryName("silver_query")
                 .trigger(availableNow=True)
                 .option("mergeSchema", "true")
                 .option("checkpointLocation", checkpoint_path)
                 .start(output_path)
                )


# COMMAND ----------

from pyspark.sql.functions import from_unixtime

weather_data = (
    spark
    .readStream
    .format("delta")
    .option("path", "dbfs:/FileStore/tables/G09/bronze_historic_weather.delta")
    .load()
)

hourly_silver_data = (
    spark
    .readStream
    .format("delta")
    .option("path", "dbfs:/FileStore/tables/G09/silver_hourly_raw.delta")
    .load()
)

weather_join_data = (
    weather_data
    .withColumn('date_timestamp', from_unixtime('dt'))
    .select("date_timestamp", "temp", "snow_1h", "pop", "rain_1h")
)

silver_hourly_trip = (
    hourly_silver_data.join(weather_join_data, "date_timestamp")
    .na.drop()
)

checkpoint_path = f"{GROUP_DATA_PATH}/silver/silver_hourly_trip_info/checkpoint"
output_path = f"{GROUP_DATA_PATH}/silver_hourly_trip_info.delta"

final_query = (
    silver_hourly_trip
    .writeStream
    .option("checkpointLocation", checkpoint_path)
    .option("mergeSchema", "true")
    .queryName("final_query")
    .outputMode("append")
    .trigger(availableNow=True)
    .format("delta")
    .start(output_path)
)


# COMMAND ----------

#%sql
#DESCRIBE HISTORY 'dbfs:/FileStore/tables/G09/silver_hourly_trip_info.delta'

# COMMAND ----------

#silver_hourly_trip_table = spark.read.format('delta').load(f"{GROUP_DATA_PATH}/silver_hourly_trip_info.delta")

# COMMAND ----------

from pyspark.sql.functions import * 
bronze_rt_station_df = spark.read.format('delta').load(BRONZE_STATION_STATUS_PATH)
bronze_rt_station_df = bronze_rt_station_df.withColumn("last_reported", to_timestamp("last_reported"))

# COMMAND ----------

bronze_rt_station_df = bronze_rt_station_df.filter(col('station_id') == "61c82689-3f4c-495d-8f44-e71de8f04088")

# COMMAND ----------

bronze_station_condensed = bronze_rt_station_df["last_reported", "num_bikes_available"]

# COMMAND ----------

bronze_station_condensed = bronze_station_condensed.withColumn("groupby_dt", date_format("last_reported",'yyyy-MM-dd HH'))
#bronze_station_condensed.orderBy('last_reported').show(10)


# COMMAND ----------

bronze_station_status_oneday = bronze_station_condensed.select('*').where((col('last_reported') >= '2023-04-29') & (col('last_reported') < '2023-04-30'))

# COMMAND ----------

bronze_station_status_oneday.sort('last_reported').show(48)

# COMMAND ----------

bronze_station_status_oneday = bronze_station_status_oneday.sort('last_reported')

# COMMAND ----------

bronze_station_status_oneday = bronze_station_status_oneday.toPandas()

# COMMAND ----------

import pandas as pd
bronze_station_status_oneday['previous_num_bike_available'] = bronze_station_status_oneday['num_bikes_available'].shift(2)
bronze_station_status_oneday['Net_Change'] = bronze_station_status_oneday['num_bikes_available']-bronze_station_status_oneday['previous_num_bike_available']
bronze_station_status_oneday_pandas = bronze_station_status_oneday.groupby(['groupby_dt']).first()

# COMMAND ----------

bronze_station_status_oneday_df=spark.createDataFrame(bronze_station_status_oneday_pandas) 

# COMMAND ----------

bronze_station_status_oneday_df.write.mode("overwrite").saveAsTable("bronze_station_status_oneday")

# COMMAND ----------

#from pyspark.sql.functions import *
#bronze_station_info_df.filter(col('name') == GROUP_STATION_ASSIGNMENT).display()

# COMMAND ----------

#bronze_weather = spark.read.format('delta').load(f"{GROUP_DATA_PATH}/bronze_historic_weather.delta")
#bronze_weather.filter(col("rain_1h") > 0).show(10)


# COMMAND ----------

import json

# Return Success
dbutils.notebook.exit(json.dumps({"exit_code": "OK"}))

# COMMAND ----------


