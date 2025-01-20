#include "tasksys.h"
#include <iostream>

IRunnable::~IRunnable() {}

ITaskSystem::ITaskSystem(int num_threads) {}
ITaskSystem::~ITaskSystem() {}

/*
 * ================================================================
 * Serial task system implementation
 * ================================================================
 */

const char *TaskSystemSerial::name()
{
    return "Serial";
}

TaskSystemSerial::TaskSystemSerial(int num_threads) : ITaskSystem(num_threads)
{
}

TaskSystemSerial::~TaskSystemSerial() {}

void TaskSystemSerial::run(IRunnable *runnable, int num_total_tasks)
{
    for (int i = 0; i < num_total_tasks; i++)
    {
        runnable->runTask(i, num_total_tasks);
    }
}

TaskID TaskSystemSerial::runAsyncWithDeps(IRunnable *runnable, int num_total_tasks,
                                          const std::vector<TaskID> &deps)
{
    // You do not need to implement this method.
    return 0;
}

void TaskSystemSerial::sync()
{
    // You do not need to implement this method.
    return;
}

/*
 * ================================================================
 * Parallel Task System Implementation
 * ================================================================
 */

const char *TaskSystemParallelSpawn::name()
{
    return "Parallel + Always Spawn";
}

TaskSystemParallelSpawn::TaskSystemParallelSpawn(int num_threads) : ITaskSystem(num_threads), maxThreads_(num_threads) {}

TaskSystemParallelSpawn::~TaskSystemParallelSpawn() {}

void TaskSystemParallelSpawn::worker(IRunnable *runnable, int startTask, int endTask, int num_total_tasks, std::mutex *mtx)
{
    for (int i = startTask; i < endTask; ++i)
    {
        mtx->lock();
        runnable->runTask(i, num_total_tasks);
        mtx->unlock();
    }
}

void TaskSystemParallelSpawn::run(IRunnable *runnable, int num_total_tasks)
{
    // set number of threads
    int i;
    int numThreads = std::min(num_total_tasks, static_cast<int>(std::thread::hardware_concurrency()));
    numThreads = std::min(numThreads, maxThreads_);

    // create threads
    std::mutex *mtx = new std::mutex();
    std::vector<std::thread> threads;
    int tasksPerThread = num_total_tasks / numThreads;
    int remainingTasks = num_total_tasks % numThreads;
    int currentTask = 0;

    // execute tasks with static assignment
    for (i = 0; i < numThreads; ++i)
    {
        int startTask = currentTask;
        int endTask = startTask + tasksPerThread + (i == 0 ? remainingTasks : 0);
        currentTask = endTask;
        threads.emplace_back(worker, runnable, startTask, endTask, num_total_tasks, mtx);
    }

    // join threads
    for (auto &t : threads)
    {
        t.join();
    }

    delete mtx;
}

TaskID TaskSystemParallelSpawn::runAsyncWithDeps(IRunnable *runnable, int num_total_tasks,
                                                 const std::vector<TaskID> &deps)
{
    // You do not need to implement this method.
    return 0;
}

void TaskSystemParallelSpawn::sync()
{
    // You do not need to implement this method.
    return;
}

/*
 * ================================================================
 * Parallel Thread Pool Spinning Task System Implementation
 * ================================================================
 */

const char *TaskSystemParallelThreadPoolSpinning::name()
{
    return "Parallel + Thread Pool + Spin";
}

TaskSystemParallelThreadPoolSpinning::TaskSystemParallelThreadPoolSpinning(int num_threads) : ITaskSystem(num_threads), maxThreads_(num_threads)
{
    threads_ = new std::thread[num_threads];

    // Create the thread pool
    for (int i = 0; i < num_threads; i++)
    {
        threads_[i] = std::thread(&TaskSystemParallelThreadPoolSpinning::worker, this);
    }
}

TaskSystemParallelThreadPoolSpinning::~TaskSystemParallelThreadPoolSpinning()
{
    terminate_ = true;

    // Wait for all threads to complete
    for (int i = 0; i < maxThreads_; i++)
    {
        if (threads_[i].joinable())
        {
            threads_[i].join();
        }
    }

    delete[] threads_;
}

void TaskSystemParallelThreadPoolSpinning::worker()
{
    while (!terminate_)
    {
        Task task;
        bool hasTask = false;

        // Get the next task
        {
            std::lock_guard<std::mutex> lock(mtx_);
            if (!task_queue_.empty())
            {
                task = task_queue_.front();
                task_queue_.pop();
                hasTask = true;
            }
        }

        // Execute the task
        if (hasTask)
        {
            task.runnable->runTask(task.currentTask, task.num_total_tasks);
            task_remaining_--;
        }
    }
}

void TaskSystemParallelThreadPoolSpinning::run(IRunnable *runnable, int num_total_tasks)
{
    running_ = true;
    task_remaining_ = num_total_tasks;

    // Add tasks to the queue
    {
        std::lock_guard<std::mutex> lock(mtx_);
        for (int i = 0; i < num_total_tasks; i++)
        {
            task_queue_.push(Task{runnable, num_total_tasks, i});
        }
    }

    // Spin until all tasks are complete
    while (task_remaining_ > 0)
    {
        // Spin
    }

    running_ = false;
}

TaskID TaskSystemParallelThreadPoolSpinning::runAsyncWithDeps(IRunnable *runnable, int num_total_tasks,
                                                              const std::vector<TaskID> &deps)
{
    // You do not need to implement this method.
    return 0;
}

void TaskSystemParallelThreadPoolSpinning::sync()
{
    // You do not need to implement this method.
    return;
}

/*
 * ================================================================
 * Parallel Thread Pool Sleeping Task System Implementation
 * ================================================================
 */

const char *TaskSystemParallelThreadPoolSleeping::name()
{
    return "Parallel + Thread Pool + Sleep";
}

TaskSystemParallelThreadPoolSleeping::TaskSystemParallelThreadPoolSleeping(int num_threads) : ITaskSystem(num_threads), maxThreads_(num_threads)
{
    threads_ = new std::thread[num_threads];

    // Create the thread pool
    for (int i = 0; i < num_threads; i++)
    {
        threads_[i] = std::thread(&TaskSystemParallelThreadPoolSleeping::worker, this);
    }
}

TaskSystemParallelThreadPoolSleeping::~TaskSystemParallelThreadPoolSleeping()
{
    terminate_ = true;

    // Wait for all threads to complete
    for (int i = 0; i < maxThreads_; i++)
    {
        if (threads_[i].joinable())
        {
            threads_[i].join();
        }
    }

    delete[] threads_;
}

void TaskSystemParallelThreadPoolSleeping::worker()
{
    while (!terminate_)
    {
        Task task;
        bool hasTask = false;

        // Get the next task
        {
            std::lock_guard<std::mutex> lock(mtx_);
            if (!task_queue_.empty())
            {
                task = task_queue_.front();
                task_queue_.pop();
                hasTask = true;
            }
        }

        // Execute the task.
        if (hasTask)
        {
            task.runnable->runTask(task.currentTask, task.num_total_tasks);
            task_remaining_--;
        }
    }
}

void TaskSystemParallelThreadPoolSleeping::run(IRunnable *runnable, int num_total_tasks)
{
    running_ = true;
    task_remaining_ = num_total_tasks;

    // Add tasks to the queue
    {
        std::lock_guard<std::mutex> lock(mtx_);
        for (int i = 0; i < num_total_tasks; i++)
        {
            task_queue_.push(Task{runnable, num_total_tasks, i});
        }
    }

    // Spin until all tasks are complete
    while (task_remaining_ > 0)
    {
        // Spin
    }

    running_ = false;
}

TaskID TaskSystemParallelThreadPoolSleeping::runAsyncWithDeps(IRunnable *runnable, int num_total_tasks,
                                                              const std::vector<TaskID> &deps)
{

    //
    // TODO: CS149 students will implement this method in Part B.
    //

    return 0;
}

void TaskSystemParallelThreadPoolSleeping::sync()
{

    //
    // TODO: CS149 students will modify the implementation of this method in Part B.
    //

    return;
}
